# PostgreSQL and backups

[The Applications](../../templates/postgres.yaml) install the CNPG operator
(wave 10), Barman plugin (20) and database resources (30). Each has an independent
health gate so custom resources are applied after their operators are ready.

| File | Purpose |
| --- | --- |
| `operator-values.yaml`, `barman-values.yaml` | Operator and plugin configuration |
| `cluster.yaml` | Single PostgreSQL instance, workload identity, TLS and managed SonarQube role |
| `pooler.yaml` | Single PgBouncer instance |
| `object-store.yaml`, `backups.yaml` | Azure backup destination and daily backup demonstration |
| `network-policy.yaml` | Database/client traffic restrictions |
| `storage-class.yaml` | Azure Premium disk class for database storage |
| `namespace.yaml`, `operator-namespace.yaml` | Database and operator namespaces |

The `postgres` Application also applies `../sonarqube/database.yaml` after the
lifecycle enables `sonarqubeReady`. CNPG reconciles the role and Database before
SonarQube starts. SonarQube currently connects directly to the `platform-rw`
service; the pooler is available separately.

The lifecycle copies the current PostgreSQL CA and role password into SonarQube's
JDBC Secret. There is one PostgreSQL instance and no synchronous standby
requirement. Backups demonstrate the Barman integration during the cluster
lifetime; the backup container and database volumes are disposable on teardown.
No cross-teardown restore or final-backup gate is configured.

The operator Application ignores only the API server's default CRD conversion
object, `strategy: None`, which is absent from the pinned chart. A conversion
webhook or any other conversion fields remain visible to Argo's diff checks.
