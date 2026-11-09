# PostgreSQL point-in-time recovery

This optional shared package restores the configured Azure PostgreSQL source into
separate recovery capacity. It is disabled by default. ASO owns the restored
server, its private endpoint, DNS zone group and Entra administrator children.
The source server, subnet and DNS zone are external references and retain their
current owners. The restored server has public access and password authentication
disabled. No source database or application connection is changed.

## Source and environment prerequisites

Provision the existing source with `make postgres-up` before enabling
`atlas.azurePostgres.enabled` and adding `environments` to
`argocd.onboarding.profiles`. The source remains Terraform-owned; ASO owns only
its separate Atlas administrator child and chart-generated application databases
and identities. Native discovery consumes repository-owned environment intent;
no application registration or database manifests are copied into GitOps.

Before a drill, verify the Atlas administrator is Ready, staging/production have
completed their database and migration waves, Kargo has selected the intended
immutable releases, and browser/data checks pass. Read current Azure and
Kubernetes state after rebuild; old identifiers and previous drill data are not
valid inputs. A healthy preview does not prove managed-server authentication.

Use one isolated recovery target for the remaining acceptance checks. Validate
PITR data and migration history before any migration change. A protected schema
downgrade may then run against that target only, using the existing application
chart's explicit `platform.atlas.migrateDown` intent. Require actual Atlas human
approval; never enable automatic approval or downgrade the source environments.
Verify the inverse is limited to the intended migration, data/history remain
correct, and source readiness is unaffected. Finally disable recovery and prove
normal ordered cleanup without restarts, finalizer removal or selective-sync
workarounds. Infrastructure readiness alone does not complete these gates.

## Operator input

The source resource group, server and location come from `atlas.azurePostgres`.
Use a distinct globally unique server name and a UTC restore point within the
source's available backup window. A new restore attempt needs a new name; changing
the timestamp of an existing server is not an in-place recovery operation.

```yaml
postgres:
  recovery:
    enabled: true
    name: example-postgres-recovery
    pointInTimeUTC: "2026-01-01T00:00:00Z"
    network:
      resourceGroup: example-network
      virtualNetwork: example-vnet
      subnet: private-endpoints
      dnsResourceGroup: example-dns
      privateDnsZone: privatelink.postgres.database.azure.com
    administrators:
      aks-admins:
        objectId: 00000000-0000-0000-0000-000000000001
        principalName: example-aks-admins
        principalType: Group
```

Supply the existing AKS Entra administrator group here. Atlas's existing managed
identity is also registered automatically, using its ASO-generated ConfigMap.
The package uses the existing namespace-scoped ASO credential and runtime account
context; it creates no credentials or role assignments. Platform operators supply
these values through the platform Application's Helm values, with their normal
reviewed configuration workflow. Do not put recovery inputs in application repos
or Terraform variables. Preserve any temporary operator override until cleanup
finishes; reseeding the bootstrap root can reset values not stored in its manifest.

ASO installs only the two required Network CRDs alongside its existing PostgreSQL
APIs. The recovery Application runs after the Atlas identity. Child waves are:
server 0; endpoint and administrators 1; DNS connection 2. The Argo ConfigMap's
ASO health checks require Ready for the current generation. Apply that ConfigMap
before activation. Healthy ARM resources alone do not prove SQL/data recovery.

## Recovery drill and evidence

1. Check the source is Ready, its backup window covers the chosen point and its
   PostgreSQL version is supported. PITR restores a whole server: both staging
   and production databases are included. It inherits the source engine,
   compute/storage and restores into the same region. Budget for this temporary
   server, storage and private endpoint until cleanup.
2. Record a committed marker in each source database before the restore point,
   and another after it. Record their database commit times and restore point in
   UTC. Never simulate loss by deleting source application data.
3. Apply the reviewed recovery intent. Record restore request time; wait for the
   recovery Application and all ASO resources to become Healthy/Ready. Verify
   `<recovery-name>.postgres.database.azure.com` resolves to the private endpoint
   from a client on the private network. Connect with TLS hostname verification
   and a short-lived Entra token using an existing authorized identity.
4. Query both restored databases: before-point markers must exist, after-point
   markers must not. Compare representative tables/counts and migration history;
   confirm source markers and both source applications remain healthy. Restore
   infrastructure readiness is not evidence that these data checks passed.
5. Record when authenticated SQL validation completes. RTO is elapsed time from
   requesting the restore to validated SQL/data availability. Report the latest
   confirmed recovered commit and the difference from the chosen restore point
   as the measured recovery-point gap; do not infer zero RPO from server Ready.
   Retain actual timestamps, marker results and cleanup evidence in the drill
   record. Do not log access tokens, tenant/subscription IDs or connection secrets.

Use native PostgreSQL/Atlas or the consumer's existing database client for these
queries from an authorized private-network runner. Do not enable automatic Atlas
migrations against the copy before validating its recovered state. Schema down
migrations are a separate protected flow, not a substitute for data recovery.

## Cleanup

Disable `postgres.recovery.enabled` in the same platform configuration. The
Application's cascade finalizer and reverse child waves remove DNS connection,
endpoint/admin children, then the recovery server. Wait for the Application and
ASO objects to disappear, then verify the Azure recovery server and endpoint are
absent. ASO remains running until deletion completes. Confirm source applications
and databases still work. Do not delete the shared subnet, DNS zone, source server
or persistent Terraform backend. Restored databases are server-owned data, not
new `FlexibleServersDatabase` resources to adopt into reconciliation.

References: [Azure PITR](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/how-to-restore-custom-restore-point),
[ASO PostgreSQL API](https://azure.github.io/azure-service-operator/reference/dbforpostgresql/v20250801/),
[ASO networking API](https://azure.github.io/azure-service-operator/reference/network/v20250301/).
