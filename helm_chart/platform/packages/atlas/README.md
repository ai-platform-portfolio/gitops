# Atlas PostgreSQL prerequisites

Each cluster installs Atlas Operator/chart **0.8.4**. Atlas resources remain in
the platform-controlled `atlas-system` namespace; dynamic preview namespaces
are not watched. Only trusted platform GitOps may author custom Atlas config:
it executes with the operator's database access.

## Startup and credentials

| Wave | Stage | Readiness dependency |
| --- | --- | --- |
| -10 | Namespaces | Platform namespaces exist |
| 0 | ESO | Secret APIs/controller available |
| 10 | Configuration | Atlas bot token and bootstrap password delivered |
| 30 | PostgreSQL | CNPG server and CA Ready |
| 35 | Atlas database access | ESO copies the bootstrap URL and CA |
| 40 | Atlas operator | Required CA volume now exists |

`configuration.yaml` reads the existing `ai-platform-portfolio-atlas-bot` Key
Vault secret via `azure-kv`. ESO generates an alphanumeric bootstrap password
once per cluster, using the existing portfolio generator pattern. Its immutable
`postgres/atlas-role-manager` Secret survives removal of the ExternalSecret and
is removed with its namespace during full teardown. It is not regenerated while
the cluster still uses it. No token or password enters Git or Helm values.

CNPG owns the new `atlas_role_manager` bootstrap login with `CREATEROLE`, without
superuser, database creation or replication privileges. Its HBA rule permits TLS
connections only to the `postgres` maintenance database. `CREATEROLE` is a
privileged platform capability, not isolation by preview name: subsequent Atlas
config must scope role inspection/plans explicitly and prove unrelated roles
remain untouched. Existing service roles retain their current ownership.

`database-access.yaml` reuses ESO's Kubernetes provider to read only this Secret
and `platform-ca` from `postgres`. ESO writes `atlas-system/atlas-postgres`,
containing `url` and `ca.crt`. The URL uses the direct
`platform-rw.postgres.svc.cluster.local` endpoint with `sslmode=verify-full` and
`sslrootcert=/etc/atlas-postgres/ca.crt`; it never uses the transaction pooler.
The operator mounts only the CA key. A named-resource Role grants its ServiceAccount
`get` on `atlas-postgres` and `atlas-token-secret`, without Secret list/watch or
cluster-wide Secret access. Future application credential names need deliberate
RBAC additions. Secret watches remain disabled; credential changes require an
Atlas resource spec change or a controlled reconciliation/restart, not an assumed
automatic Secret watch.

Future central Atlas resources use `urlFrom.secretKeyRef` for the bootstrap URL
and `cloud.tokenFrom` for the bot token. Operator-wide `ATLAS_TOKEN` alone does
not authorize remote migration directories. Custom configuration is enabled for
future scoped role plans; it must keep the same verified connection settings.
No AtlasSchema, AtlasMigration, preview database or schema is created here.

## Health and teardown

The bootstrap Argo ConfigMap gives AtlasSchema and AtlasMigration a health gate
that checks both the current observed generation and Ready condition; stale
success cannot start dependent workloads. Apply this ConfigMap through the
normal bootstrap workflow before relying on those gates in a later deployment.

`make down` refuses to remove the platform while AtlasSchema/AtlasMigration,
standalone CNPG DatabaseRole, or databases outside the tracked platform database
inventory remain. Complete preview SQL cleanup and wait for finalizers first:
deleting an Atlas CR alone does not delete its SQL objects. It never removes
finalizers to bypass failed cleanup. Atlas shuts down before its copied
credentials, PostgreSQL and the original bootstrap credential are removed.

Persistence remains disabled for this idle prerequisite. Before adopting live
Atlas resources, assess the recommended grant-cache PVC against AKS disk
attachment headroom and verify its permissions; the last cluster startup was
already constrained by disk attachments. No additional node capacity is assumed.

`make validate` checks rendered credentials, limited RBAC, verified TLS, role
privileges, health configuration and startup waves. Lifecycle tests cover
teardown blockers and API failures. These are not live PostgreSQL authentication,
Atlas licence, migration or cold-start acceptance tests. Run those in the next
approved disposable integration stage, including two-preview role isolation.

Sources: [operator credentials](https://atlasgo.io/integrations/kubernetes/versioned),
[security as code](https://atlasgo.io/guides/postgres/security-declarative),
[grant cache](https://atlasgo.io/integrations/kubernetes/install),
[CNPG roles](https://cloudnative-pg.io/docs/1.30/declarative_role_management/).
