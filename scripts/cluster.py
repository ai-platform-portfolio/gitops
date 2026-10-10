#!/usr/bin/env python3
"""Coordinate the private Terraform configuration and this GitOps deployment."""
import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from .postgres import Postgres
except ImportError:
    from postgres import Postgres


EXPECTED_PHASES = {"flux-system", "envoy-gateway", "external-secrets", "cert-manager",
                   "external-dns", "configuration", "lgtm", "opentelemetry", "langfuse",
                   "postgres-operator", "postgres"}
EXPECTED_RELEASES = {
    ("envoy-gateway-system", "envoy-gateway"), ("external-secrets", "external-secrets"),
    ("cert-manager", "cert-manager"), ("external-dns", "external-dns"),
    ("lgtm", "grafana"), ("lgtm", "loki"), ("lgtm", "mimir"), ("lgtm", "tempo"),
    ("monitoring", "k8s-monitoring"), ("opentelemetry", "opentelemetry"),
    ("langfuse", "clickhouse-operator"), ("langfuse", "langfuse"),
    ("cnpg-system", "cloudnative-pg"), ("cnpg-system", "plugin-barman-cloud"),
}


def run(*args, capture=False):
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def wait_for(check, description, timeout=1800):
    print(description, flush=True)
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            raise TimeoutError(description)
        time.sleep(10)


class Cluster:
    def __init__(self, infra):
        self.tf = ["terraform", f"-chdir={infra}"]
        self.kube = None

    def apply(self, cluster, flux):
        run(*self.tf, "apply", "-auto-approve",
            f"-var=cluster_enabled={str(cluster).lower()}",
            f"-var=flux_enabled={str(flux).lower()}")

    def connect(self, kubeconfig):
        # Also works before the new output/moved blocks have been applied.
        output = subprocess.run([*self.tf, "output", "-json", "aks_connection"],
                                text=True, capture_output=True)
        connection = json.loads(output.stdout) if output.returncode == 0 else None
        if output.returncode != 0:
            state = json.loads(run(*self.tf, "show", "-json", capture=True))
            resources = state.get("values", {}).get("root_module", {}).get("resources", [])
            aks = next((r["values"] for r in resources
                        if r["type"] == "azurerm_kubernetes_cluster" and r["name"] == "aks"), None)
            if aks:
                connection = {"name": aks["name"], "resource_group": aks["resource_group_name"],
                              "subscription_id": aks["id"].split("/")[2]}
        if connection is None:
            return False
        run("az", "aks", "get-credentials", "--subscription", connection["subscription_id"],
            "--resource-group", connection["resource_group"], "--name", connection["name"],
            "--file", str(kubeconfig), "--overwrite-existing")
        run("kubelogin", "convert-kubeconfig", "--kubeconfig", str(kubeconfig), "-l", "azurecli")
        self.kube = ["kubectl", "--kubeconfig", str(kubeconfig)]
        self.connection = connection
        return True

    def objects(self, kind, namespace=None):
        scope = ["-n", namespace] if namespace else ["-A"]
        return json.loads(run(*self.kube, "get", kind, *scope, "-o", "json", capture=True))["items"]

    def delete(self, kind, names, namespace=None):
        # Discover first: optional chart CRDs may already have been uninstalled.
        api = run(*self.kube, "api-resources", "-o", "name", capture=True).splitlines()
        if not any(r == kind or r.startswith(kind + ".") for r in api):
            return
        present = {o["metadata"]["name"] for o in self.objects(kind, namespace)}
        names = sorted(set(names) & present)
        if not names:
            return
        scope = ["-n", namespace] if namespace else []
        run(*self.kube, "delete", kind, *names, *scope, "--ignore-not-found", "--wait=false")
        wait_for(lambda: not any(o["metadata"]["name"] in names
                                for o in self.objects(kind, namespace)), f"Waiting for {kind}: {', '.join(names)}")

    def ready(self):
        # Recover an interrupted teardown without leaving GitOps suspended.
        run(*self.kube, "-n", "flux-system", "annotate", "fluxinstance", "flux",
            "fluxcd.controlplane.io/reconcile=enabled", "--overwrite")
        resumed = False

        def healthy(objects):
            return all(any(c["type"] == "Ready" and c["status"] == "True"
                           and c.get("observedGeneration", o["metadata"]["generation"]) == o["metadata"]["generation"]
                           for c in o.get("status", {}).get("conditions", [])) for o in objects)

        def reconciled():
            nonlocal resumed
            api = run(*self.kube, "api-resources", "-o", "name", capture=True).splitlines()
            if not all(any(r.startswith(kind + ".") for r in api)
                       for kind in ["kustomizations", "helmreleases"]):
                return False
            phases = self.objects("kustomizations", "flux-system")
            if not resumed and any(o["metadata"]["name"] == "flux-system" for o in phases):
                for phase in phases:
                    if phase["spec"].get("suspend"):
                        run(*self.kube, "-n", "flux-system", "patch", "kustomization",
                            phase["metadata"]["name"], "--type=merge", "-p", '{"spec":{"suspend":false}}')
                run(*self.kube, "-n", "flux-system", "annotate", "kustomization", "flux-system",
                    f"reconcile.fluxcd.io/requestedAt={time.time_ns()}", "--overwrite")
                resumed = True
                return False
            releases = self.objects("helmreleases")
            phase_names = {o["metadata"]["name"] for o in phases}
            release_names = {(o["metadata"]["namespace"], o["metadata"]["name"]) for o in releases}
            return (EXPECTED_PHASES <= phase_names and EXPECTED_RELEASES <= release_names
                    and healthy(phases) and healthy(releases))

        wait_for(reconciled, "Waiting for all eleven Flux phases and fourteen Helm releases", timeout=3600)
        print("GitOps is Ready.", flush=True)

    def cleanup(self):
        instances = self.objects("fluxinstances", "flux-system")
        if instances:
            run(*self.kube, "-n", "flux-system", "annotate", "fluxinstance", "flux",
                "fluxcd.controlplane.io/reconcile=disabled", "--overwrite")
        for phase in self.objects("kustomizations", "flux-system"):
            run(*self.kube, "-n", "flux-system", "patch", "kustomization", phase["metadata"]["name"],
                "--type=merge", "-p", '{"spec":{"suspend":true}}')

        # Keep the ClickHouse operator alive until database finalizers finish.
        self.delete("helmreleases", ["langfuse"], "langfuse")
        self.delete("clickhouseclusters", ["langfuse"], "langfuse")
        self.delete("keeperclusters", ["langfuse"], "langfuse")
        self.delete("helmreleases", ["opentelemetry"], "opentelemetry")
        self.delete("helmreleases", ["grafana", "loki", "mimir", "tempo"], "lgtm")
        self.delete("helmreleases", ["k8s-monitoring"], "monitoring")
        self.delete("namespaces", ["langfuse", "opentelemetry", "monitoring", "lgtm"])

        # Backup/restore metadata must be durable before any database PVC is removed.
        # Keep both database controllers and cert-manager alive for finalizers.
        api = run(*self.kube, "api-resources", "-o", "name", capture=True).splitlines()
        if "clusters.postgresql.cnpg.io" in api:
            databases = self.objects("clusters.postgresql.cnpg.io", "postgres")
            if any(o["metadata"]["name"] == "platform" for o in databases):
                self.delete("scheduledbackups.postgresql.cnpg.io", ["platform-daily"], "postgres")
                Postgres(self, run, wait_for).backup(shutdown=True)
        self.delete("scheduledbackups.postgresql.cnpg.io", ["platform-daily"], "postgres")
        self.delete("clusters.postgresql.cnpg.io", ["platform"], "postgres")
        self.delete("namespaces", ["postgres"])
        self.delete("configmaps", ["postgres-settings"], "flux-system")
        self.delete("storageclasses", ["postgres-premium"])
        self.delete("helmreleases", ["plugin-barman-cloud"], "cnpg-system")
        self.delete("helmreleases", ["cloudnative-pg"], "cnpg-system")
        self.delete("namespaces", ["cnpg-system"])

        self.delete("gateways", ["public"], "envoy-gateway-system")
        self.delete("certificates", ["wildcard-dev"], "envoy-gateway-system")
        self.delete("clusterissuers", ["letsencrypt-azuredns"])
        self.delete("clustersecretstores", ["azure-kv"])
        self.delete("gatewayclasses", ["envoy"])
        # Let ExternalDNS remove owned records after routes/Gateway disappear.
        if any(o["metadata"]["name"] == "external-dns" for o in self.objects("helmreleases")):
            print("Allowing ExternalDNS one sync interval for record cleanup", flush=True)
            until = time.monotonic() + 70
            while time.monotonic() < until:
                time.sleep(min(10, until - time.monotonic()))
        for namespace in ["external-dns", "envoy-gateway-system", "external-secrets", "cert-manager"]:
            self.delete("helmreleases", [namespace], namespace)
            self.delete("namespaces", [namespace])
        # The root/children stay suspended. Permit Operator finalizer processing.
        if instances:
            run(*self.kube, "-n", "flux-system", "annotate", "fluxinstance", "flux",
                "fluxcd.controlplane.io/reconcile=enabled", "--overwrite")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "down", "plan"])
    parser.add_argument("--infra-dir", type=Path, required=True)
    args = parser.parse_args()
    infra = args.infra_dir.resolve()
    if not (infra / "main.tf").is_file():
        parser.error(f"Terraform checkout not found: {infra}")
    if args.action == "up" and not (infra / "postgres.tf").is_file():
        parser.error(f"PostgreSQL infrastructure file missing: {infra / 'postgres.tf'}")
    for command in ["terraform", "az", "kubectl", "kubelogin"]:
        if not shutil.which(command):
            parser.error(f"Install {command} before running this command")
    cluster = Cluster(infra)
    run("az", "account", "show", "--output", "none")
    run(*cluster.tf, "init", "-input=false")
    if args.action == "plan":
        run(*cluster.tf, "plan", "-var=cluster_enabled=true", "-var=flux_enabled=true")
        return
    with tempfile.TemporaryDirectory(prefix="portfolio-aks-") as tmp:
        kubeconfig = Path(tmp) / "kubeconfig"
        if args.action == "up":
            cluster.apply(True, True)
            if not cluster.connect(kubeconfig):
                raise RuntimeError("Terraform did not create AKS")
            postgres = Postgres(cluster, run, wait_for)
            postgres.preflight()
            postgres.prepare()
            postgres.resume()
            cluster.ready()
            postgres.backup()
        else:
            if cluster.connect(kubeconfig):
                cluster.cleanup()
                cluster.apply(True, False)
            cluster.apply(False, False)
            print("AKS removed; shared Azure resources, identity and Blob storage retained.", flush=True)


if __name__ == "__main__":
    main()
