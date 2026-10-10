"""Bootstrap SonarQube using the platform's retained PostgreSQL backup storage.

Run only through make up, with the same Azure identity as PostgreSQL lifecycle.
Secrets are never passed as command arguments or printed. Lifecycle commands
must run serially, as for the existing PostgreSQL recovery marker.
"""
import base64
import json
import re
import secrets
import subprocess
import tempfile
from pathlib import Path


CREDENTIALS = "lifecycle/sonarqube.json"


def validate_credentials(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported SonarQube credential record")
    for key in ("jdbc-password", "admin-password", "monitoring-passcode"):
        if not isinstance(value.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value[key]):
            raise ValueError(f"Invalid SonarQube credential field: {key}")
    return value


class SonarQube:
    def __init__(self, cluster, postgres, run, wait):
        self.cluster, self.postgres, self.run, self.wait = cluster, postgres, run, wait

    def credentials(self, database_exists):
        with tempfile.TemporaryDirectory(prefix="sonarqube-credentials-") as directory:
            path = Path(directory) / "credentials.json"
            if self.postgres.exists(CREDENTIALS):
                self.run("az", "storage", "blob", "download", *self.postgres.storage,
                         "--name", CREDENTIALS, "--file", str(path), "--output", "none")
                return validate_credentials(json.loads(path.read_text()))
            if database_exists:
                raise RuntimeError("SonarQube database exists without retained credentials; "
                                   "restore lifecycle/sonarqube.json before continuing")
            value = {"version": 1, **{key: secrets.token_urlsafe(32) for key in
                     ("jdbc-password", "admin-password", "monitoring-passcode")}}
            path.touch(mode=0o600)
            path.write_text(json.dumps(value))
            self.run("az", "storage", "blob", "upload", *self.postgres.storage,
                     "--name", CREDENTIALS, "--file", str(path), "--overwrite", "false",
                     "--output", "none")
            # Persist and verify before provisioning SQL or installing the application.
            path.unlink()
            self.run("az", "storage", "blob", "download", *self.postgres.storage,
                     "--name", CREDENTIALS, "--file", str(path), "--output", "none")
            if validate_credentials(json.loads(path.read_text())) != value:
                raise RuntimeError("SonarQube credential read-back failed")
            return value

    def private_command(self, args, content):
        result = subprocess.run(args, input=content, text=True, capture_output=True)
        if result.returncode:
            # SQL diagnostics can contain secrets, so do not include stderr/input.
            raise RuntimeError("SonarQube bootstrap command failed; AKS has been retained")

    def sql(self, script):
        primary = self.postgres.database()["status"]["currentPrimary"]
        self.private_command([*self.cluster.kube, "exec", "-i", "-n", "postgres", primary,
                              "-c", "postgres", "--", "psql", "-X", "-U", "postgres",
                              "-d", "postgres", "-v", "ON_ERROR_STOP=1"], script)

    def secret(self, name, values):
        doc = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
               "metadata": {"name": name, "namespace": "sonarqube"},
               "data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}}
        # Server-side apply avoids storing a second secret copy in last-applied annotations.
        self.private_command([*self.cluster.kube, "apply", "--server-side",
                              "--field-manager=sonarqube-lifecycle", "-f", "-"], json.dumps(doc))

    def prepare(self):
        def ready():
            database = self.postgres.database()
            return bool(database and database.get("status", {}).get("currentPrimary") and
                        any(c["type"] == "Ready" and c["status"] == "True"
                            for c in database.get("status", {}).get("conditions", [])))

        self.wait(ready, "Waiting for PostgreSQL before preparing SonarQube", timeout=3600)
        exists = self.postgres.sql("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='sonarqube')") == "t"
        value = self.credentials(exists)
        # Validation restricts the password alphabet; no SQL quoting ambiguity.
        # Keep the application a non-superuser owning only its dedicated database.
        self.sql("SET log_statement = 'none';\nSET log_min_error_statement = 'panic';\n"
                 "SELECT 'CREATE ROLE sonarqube LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION' "
                 "WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='sonarqube')\\gexec\n"
                 f"ALTER ROLE sonarqube PASSWORD '{value['jdbc-password']}';\n"
                 "SELECT 'CREATE DATABASE sonarqube OWNER sonarqube ENCODING ''UTF8'' TEMPLATE template0' "
                 "WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='sonarqube')\\gexec\n"
                 "REVOKE CONNECT ON DATABASE sonarqube FROM PUBLIC;\n"
                 "\\connect sonarqube\nREVOKE CREATE ON SCHEMA public FROM PUBLIC;\n")
        ca = json.loads(self.run(*self.cluster.kube, "get", "secret", "platform-ca", "-n", "postgres",
                                 "-o", "json", capture=True))["data"]["ca.crt"]
        # Flux owns the namespace. Apply its exact definition before creating secrets.
        namespace = Path(__file__).resolve().parents[1] / "flux/installs/sonarqube/namespace.yaml"
        self.run(*self.cluster.kube, "apply", "--server-side", "--field-manager=kustomize-controller",
                 "-f", str(namespace))
        self.secret("sonarqube-jdbc", {"jdbc-password": value["jdbc-password"],
                                       "ca.crt": base64.b64decode(ca).decode()})
        self.secret("sonarqube-monitoring", {"passcode": value["monitoring-passcode"]})
        self.secret("sonarqube-admin", {"password": value["admin-password"], "currentPassword": "admin"})
        print("SonarQube database, credentials and current PostgreSQL CA prepared.", flush=True)
