# Disposable application acceptance caller

This is one manually selected deployment, before PR ApplicationSet automation.
`preview.enabled` defaults to false and is restricted to the management cluster.
When enabled, Argo creates `preview-manual` with manual sync and no cascade finalizer.
Merging these files does not enable or sync the application.

`values.yaml` is the application chart interface. Its two image digests and Atlas
migration tag come from the same successful [main publication](https://github.com/ai-platform-portfolio/preview-environments/actions/runs/41315554435),
source `a429264bbc5960e7eacba1499e255f517a39ff48`. The migration checksum is recorded
beside them. This is a reviewed manual selection; automated artifact freshness,
PR revision updates and merge checks remain later work.

One Argo Application combines the pinned application chart with the existing
resources chart for package-owned credentials. Their waves run together:
namespace -> generated passwords/reader access -> copied credentials -> Atlas
roles -> CNPG database -> access migration -> registry application migration ->
workloads/routes. The API uses database-aware readiness and independent liveness;
nginx forwards `/api` to the chart's `api` Service. The single public hostname is
`preview-manual.<DNS_ZONE>`, with its certificate generated from the ListenerSet.

ESO generates stable, immutable source passwords in `postgres` and copies them to
`atlas-system` for Atlas. A separate named reader can copy only the runtime password
and public CA into `preview-manual`. Migrator and role-manager credentials never
enter application pods. The CA projection contains only `ca.crt`; DATABASE_URL is
injected only into the API. Connections use the direct CNPG service with verify-full
TLS. CNPG owns the database; Atlas owns only the three explicitly scoped roles;
access and application migrations have separate private histories. HBA permits
only the two preview logins on this database, before the existing reject-all rule.

Before enabling or syncing:

- Merge the prerequisite [chart PR7](https://github.com/ai-platform-portfolio/application-helm-chart/pull/7)
  and update the Application pin to the actual merged commit.
- Apply the already-merged bootstrap Argo ConfigMap through its existing owner.
  AtlasSchema/AtlasMigration current-generation health checks are absent live;
  waves alone do not gate migration success.
- Verify PostgreSQL/Atlas/ESO readiness, the existing `atlas-postgres` SecretStore,
  named reader access, CA, bot entitlement and the `preview-roles` schema plan
  repository. The read-only Atlas listing currently exposes only the
  `preview-environments` migration-directory repository, so the schema plan
  repository is not verified. Keep this separate from migration publication.
- Obtain deployment approval, enable the caller through GitOps, then sync it
  explicitly. Prove HTTPS save/read, SQL restrictions and current-generation readiness.

No application deployment or SQL reconciliation has been proved by rendering.
Keep this caller's names and roleVars stable; change access with new forward SQL
migrations. Data is disposable. Do not delete the Application to clean up: stop
workloads/migrations, drop this database and wait, remove its Atlas-owned roles
while their scope/credentials remain, then remove Kubernetes resources and source
passwords. Removing Atlas CRs alone leaves SQL roles behind. Ordered, repeatable
cleanup is the next lifecycle task; unattended prune/cascade remains disabled.

Sources: [Argo multi-source Applications](https://argo-cd.readthedocs.io/en/stable/user-guide/multiple_sources/),
[Atlas repository types](https://atlasgo.io/cloud/features/registry).
