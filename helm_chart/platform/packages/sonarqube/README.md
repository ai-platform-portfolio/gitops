# Code quality

[The Applications](../../templates/sonarqube.yaml) install SonarQube Developer at
wave 60 and its configuration job at wave 70, once `sonarqubeReady` is true.

| File | Owning Application | Purpose |
| --- | --- | --- |
| `values.yaml` | sonarqube | External PostgreSQL JDBC connection and mounted CA |
| `database.yaml` | postgres | CNPG-managed SonarQube Database, before SonarQube starts |
| `network-policy.yaml` | configuration | Restrict traffic to the application and PostgreSQL |
| `configure-job.yaml`, `configure.sh` | sonarqube-configure | Admin password, permissions, base URL and Entra SAML settings |
| `networking.yaml` | routes | ListenerSet, HTTPS route and redirect after backend health |
| `namespace.yaml` | namespaces | Public-gateway label and pod-security settings |

`helm_chart/scripts/credentials.py` prepares the role/admin/monitoring Secrets and copies
PostgreSQL's current CA into the JDBC Secret. The lifecycle then enables the
managed role in `../postgres/cluster.yaml`, this Database, application and routes.
Argo checks Database reconciliation before advancing to the SonarQube wave.

SonarQube Developer requires a licence. The existing Entra service principal and
active public SAML certificate must be discoverable by the authenticated lifecycle
identity. Application credentials and database contents are disposable on teardown.
