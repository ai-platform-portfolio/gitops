"""Retain PostgreSQL recovery metadata in Azure Blob, outside the AKS lifecycle."""
import json
import re
import tempfile
import uuid
from pathlib import Path


NAMESPACE = "postgres"
CLUSTER = "platform"
SETTINGS = "postgres-settings"
MARKER = "lifecycle/platform.json"
PLUGIN = "barman-cloud.cloudnative-pg.io"


def bootstrap(marker):
    """Reject malformed state rather than silently initializing an empty database."""
    if marker is None:
        return {"initdb": {"database": "app", "owner": "app"}}
    if not isinstance(marker, dict) or marker.get("version") != 1 or marker.get("postgres_major") != 18:
        raise ValueError("Unsupported PostgreSQL recovery marker")
    if not re.fullmatch(r"platform-[0-9a-f]{32}", marker.get("server_name", "")):
        raise ValueError("Invalid recovery archive name")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}", marker.get("backup_id", "")):
        raise ValueError("Invalid recovery backup ID")
    target = {"backupID": marker["backup_id"]}
    if "target_name" in marker:
        if not re.fullmatch(r"shutdown_[0-9a-f]{32}", marker["target_name"]):
            raise ValueError("Invalid recovery target")
        target["targetName"] = marker["target_name"]
    return {"recovery": {"database": "app", "owner": "app", "source": "previous",
                         "recoveryTarget": target}}


class Postgres:
    def __init__(self, cluster, run, wait):
        self.cluster, self.run, self.wait = cluster, run, wait
        connection = json.loads(run(*cluster.tf, "output", "-json", "postgres_connection", capture=True))
        if connection.get("container") != "postgres-backups":
            raise ValueError("Unexpected PostgreSQL backup container")
        self.connection = connection
        self.storage = ["--account-name", connection["storage_account"],
                        "--container-name", connection["container"], "--auth-mode", "login",
                        "--subscription", connection["subscription_id"], "--only-show-errors"]

    def preflight(self):
        connection = self.cluster.connection
        profile = json.loads(self.run(
            "az", "aks", "show", "--subscription", connection["subscription_id"],
            "--resource-group", connection["resource_group"], "--name", connection["name"],
            "--query", "{policy:networkProfile.networkPolicy,version:kubernetesVersion,"
            "oidc:oidcIssuerProfile.enabled,workload:securityProfile.workloadIdentity.enabled}",
            "-o", "json", capture=True))
        if profile.get("policy") not in {"calico", "cilium", "azure"}:
            raise RuntimeError("Enable an AKS network policy engine before deploying PostgreSQL")
        if not profile.get("oidc") or not profile.get("workload"):
            raise RuntimeError("Enable AKS OIDC issuer and Workload Identity before deploying PostgreSQL")
        version = profile.get("version", "").split(".")
        if len(version) < 2 or version[0] != "1" or version[1] not in {"34", "35", "36"}:
            raise RuntimeError("Pinned CloudNativePG requires a supported Kubernetes version: 1.34–1.36")

    def manifest(self, doc):
        with tempfile.TemporaryDirectory(prefix="postgres-manifest-") as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(doc))
            self.run(*self.cluster.kube, "apply", "-f", str(path))

    def exists(self, name):
        return json.loads(self.run("az", "storage", "blob", "exists", *self.storage,
                                   "--name", name, "-o", "json", capture=True))["exists"]

    def read_marker(self):
        if not self.exists(MARKER):
            # Missing marker is safe only when there is no previous archive.
            blobs = json.loads(self.run("az", "storage", "blob", "list", *self.storage,
                                        "--prefix", "archive/", "--num-results", "1",
                                        "--query", "[].name", "-o", "json", capture=True))
            if blobs:
                raise RuntimeError("Backup archive exists without a recovery marker; recover explicitly")
            return None
        with tempfile.TemporaryDirectory(prefix="postgres-marker-") as tmp:
            path = Path(tmp) / "marker.json"
            self.run("az", "storage", "blob", "download", *self.storage, "--name", MARKER,
                     "--file", str(path), "--output", "none")
            marker = json.loads(path.read_text())
        bootstrap(marker)
        backup = f"archive/{marker['server_name']}/base/{marker['backup_id']}/backup.info"
        if not self.exists(backup):
            raise RuntimeError("Recovery marker points to a missing base backup; refusing fresh initialization")
        return marker

    def prepare(self):
        """Create required Flux inputs, or preserve an existing installation's archive."""
        maps = self.cluster.objects("configmaps", "flux-system")
        existing = next((doc for doc in maps if doc["metadata"]["name"] == SETTINGS), None)
        if existing:
            data = existing["data"]
        else:
            marker = self.read_marker()
            data = {
                "POSTGRES_ARCHIVE_SERVER": "platform-" + uuid.uuid4().hex,
                "POSTGRES_RECOVERY_SERVER": marker["server_name"] if marker else "unused",
                "POSTGRES_BOOTSTRAP": json.dumps(bootstrap(marker)),
            }
        data.update({
            "POSTGRES_STORAGE_ACCOUNT": self.connection["storage_account"],
            "POSTGRES_AZURE_CLIENT_ID": self.connection["client_id"],
            "POSTGRES_AZURE_TENANT_ID": self.connection["tenant_id"],
        })
        self.manifest({"apiVersion": "v1", "kind": "ConfigMap",
                       "metadata": {"name": SETTINGS, "namespace": "flux-system",
                                    "labels": {"reconcile.fluxcd.io/watch": "Enabled"}},
                       "data": data})
        return not existing

    def databases(self):
        api = self.run(*self.cluster.kube, "api-resources", "-o", "name", capture=True).splitlines()
        if "clusters.postgresql.cnpg.io" not in api:
            return []
        return self.cluster.objects("clusters.postgresql.cnpg.io", NAMESPACE)

    def database(self):
        return next((doc for doc in self.databases() if doc["metadata"]["name"] == CLUSTER), None)

    def resume(self):
        if self.database():
            self.run(*self.cluster.kube, "annotate", "clusters.postgresql.cnpg.io", CLUSTER,
                     "-n", NAMESPACE, "cnpg.io/hibernation=off", "--overwrite")

    def sql(self, query):
        database = self.database()
        primary = database.get("status", {}).get("currentPrimary") if database else None
        if not primary:
            raise RuntimeError("PostgreSQL has no primary; refusing teardown")
        return self.run(*self.cluster.kube, "exec", "-n", NAMESPACE, primary, "-c", "postgres",
                        "--", "psql", "-U", "postgres", "-d", "postgres", "-At",
                        "-v", "ON_ERROR_STOP=1", "-c", query, capture=True).strip()

    def backup(self, shutdown=False):
        database = self.database()
        if database is None:
            return
        if shutdown:
            self.wait(lambda: not any(b.get("status", {}).get("phase") not in {"completed", "failed"}
                                      for b in self.cluster.objects("backups.postgresql.cnpg.io", NAMESPACE)),
                      "Waiting for any earlier PostgreSQL backups to finish", timeout=3600)
            # Reconciliation is already suspended by Cluster.cleanup. Remove the
            # only client entrypoint, wait for pooler pods, then close old sessions.
            self.cluster.delete("poolers.postgresql.cnpg.io", ["platform-pooler"], NAMESPACE)
            self.wait(lambda: not any(p["metadata"].get("labels", {}).get("app") == "platform-pooler"
                                      for p in self.cluster.objects("pods", NAMESPACE)),
                      "Waiting for PostgreSQL client connections to stop", timeout=600)
            self.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                     "WHERE backend_type = 'client backend' AND usename <> 'postgres' "
                     "AND pid <> pg_backend_pid()")
        name = "lifecycle-" + uuid.uuid4().hex
        self.manifest({"apiVersion": "postgresql.cnpg.io/v1", "kind": "Backup",
                       "metadata": {"name": name, "namespace": NAMESPACE},
                       "spec": {"cluster": {"name": CLUSTER}, "target": "primary", "method": "plugin",
                                "pluginConfiguration": {"name": PLUGIN}}})
        status = {}

        def completed():
            nonlocal status
            backup = next(doc for doc in self.cluster.objects("backups.postgresql.cnpg.io", NAMESPACE)
                          if doc["metadata"]["name"] == name)
            status = backup.get("status", {})
            if status.get("phase") == "failed":
                raise RuntimeError(f"PostgreSQL backup {name} failed; AKS has been retained")
            return status.get("phase") == "completed"

        self.wait(completed, "Waiting for PostgreSQL backup before continuing", timeout=3600)
        plugin = next(p for p in database["spec"]["plugins"] if p["name"] == PLUGIN)
        marker = {"version": 1, "postgres_major": 18,
                  "server_name": plugin["parameters"]["serverName"],
                  "backup_id": status.get("backupID", "")}
        if shutdown:
            target = "shutdown_" + uuid.uuid4().hex
            self.sql(f"SELECT pg_create_restore_point('{target}')")
            wal = self.sql("SELECT pg_walfile_name(pg_current_wal_lsn())")
            if not re.fullmatch(r"[0-9A-F]{24}", wal):
                raise RuntimeError("Could not determine shutdown WAL segment")
            self.sql("SELECT pg_switch_wal()")
            self.wait(lambda: self.sql(f"SELECT COALESCE(last_archived_wal >= '{wal}', false) "
                                      "FROM pg_stat_archiver") == "t",
                      "Waiting for the shutdown restore point to reach Azure Blob", timeout=600)
            marker["target_name"] = target
        bootstrap(marker)
        if not self.exists(f"archive/{marker['server_name']}/base/{marker['backup_id']}/backup.info"):
            raise RuntimeError("Completed backup is not visible in Blob; refusing teardown")
        with tempfile.TemporaryDirectory(prefix="postgres-marker-") as tmp:
            path = Path(tmp) / "marker.json"
            path.write_text(json.dumps(marker))
            self.run("az", "storage", "blob", "upload", *self.storage, "--name", MARKER,
                     "--file", str(path), "--overwrite", "true", "--output", "none")
        # A read-back must succeed before deleting the database or its PVCs.
        if self.read_marker() != marker:
            raise RuntimeError("Recovery marker verification failed; refusing teardown")
        print("PostgreSQL recovery metadata verified in retained Azure Blob storage.", flush=True)
