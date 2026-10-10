# PostgreSQL on AKS

An independent CloudNativePG `platform` cluster runs in `postgres`. It joins the
normal Flux graph and `make up` / `make down` workflow. This does not replace or
migrate Langfuse's bundled database.

## Research and choice

Checked against primary documentation on 10 October 2026.

| Option | Assessment for this repo |
| --- | --- |
| CloudNativePG + Barman plugin | Selected: replication/failover, declarative PgBouncer, Azure object-store recovery and federation, using the existing Flux and cert-manager installation. |
| Crunchy PGO | Credible HA/pooling/backup alternative; introduces its pgBackRest and StatefulSet model, with no existing PGO installation here to reuse. |
| Azure PostgreSQL Flexible Server | Built-in Entra SQL authentication and independence from AKS deletion; an Azure-managed service rather than the requested database inside AKS. |
| Standalone PostgreSQL chart | Smaller footprint; separate failover and recovery orchestration would be needed to meet these requirements. |

See the [CloudNativePG architecture](https://cloudnative-pg.io/docs/1.30/architecture/),
[PGO architecture](https://access.crunchydata.com/documentation/postgres-operator/latest/architecture),
and [Flexible Server Entra authentication](https://learn.microsoft.com/en-us/azure/postgresql/security/security-entra-configure).

| Component | Pin |
| --- | --- |
| CloudNativePG chart / operator | 0.29.1 / 1.30.1 |
| Barman chart / controller and sidecar | 0.8.1 / v0.15.1 |
| PostgreSQL | 18.6-system-trixie |
| PgBouncer | 1.26.0 |

The [operator chart](https://github.com/cloudnative-pg/charts/releases/tag/cloudnative-pg-v0.29.1)
and [plugin chart](https://github.com/cloudnative-pg/charts/releases/tag/plugin-barman-cloud-v0.8.1)
are pinned separately. [PostgreSQL 18.6](https://www.postgresql.org/docs/release/18.6/)
contains security fixes; 18.5 was never released. [PgBouncer 1.26.0](https://www.pgbouncer.org/changelog.html)
also fixes authentication vulnerabilities; its [image is published upstream](https://github.com/cloudnative-pg/pgbouncer-containers/pkgs/container/pgbouncer).
Review security releases and update these pins regularly.

The target is Kubernetes 1.34–1.36 for this operator minor. PostgreSQL does not
pin the AKS version; the lifecycle preflight checks the running cluster for compatibility.
See [CloudNativePG support policy](https://cloudnative-pg.io/docs/1.30/supported_releases/).

## Identity and security

The separate `portfolio-infra/postgres.tf` creates a dedicated managed identity with
federation for `system:serviceaccount:postgres:platform`, the current AKS OIDC
issuer and audience `api://AzureADTokenExchange`. CloudNativePG creates this
service account and labels database/recovery pods for Azure token injection.
Barman uses `inheritFromAzureAD`, with no storage key, SAS token or client secret.
The Azure SDK managed identity credential recognizes the projected token file.
See [AKS Workload Identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview),
[Barman Azure configuration](https://cloudnative-pg.io/plugin-barman-cloud/docs/0.15.0/object_stores/),
and the [Azure SDK implementation](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/identity/azure-identity/azure/identity/_credentials/managed_identity.py).

`Storage Blob Data Contributor` is scoped to the backup container. PostgreSQL
has no DNS, Key Vault or LGTM storage permissions. The existing cluster-admin
Entra user/group gets the same container data role for the lifecycle CLI.
The dedicated ZRS account disables shared keys/public blobs, requires TLS 1.2,
and enables versions and 30-day blob/container soft delete. Azure encrypts stored
data. The authenticated endpoint is publicly reachable; a private endpoint and
its DNS/routes would require a separate network topology change.

SQL access is private ClusterIP only, with TLS/SCRAM and remote superuser access
disabled. The namespace enforces restricted Pod Security. The operator manages
replication/pooler certificates. Both namespace and pod labels authorize client
traffic to PgBouncer. Internal replication, controller access, monitoring,
DNS and HTTPS egress are allowed explicitly. Standard NetworkPolicy cannot match
HTTPS hostnames, so outbound TCP 443 is allowed. The AKS patch enables Calico;
startup checks for an actual policy engine. See [AKS NetworkPolicy](https://learn.microsoft.com/en-us/azure/aks/use-network-policies).

Federation authenticates Azure access independently of database login.
PostgreSQL 18 OAuth needs a provider-specific validator, issuer/audience checks,
identity-to-role mapping and compatible clients. This installation does not
bundle an Entra validator, so SQL OAuth is not enabled. A web OAuth proxy cannot
authenticate the PostgreSQL wire protocol. For turnkey Entra SQL login, use
Flexible Server; a custom validator image needs separate implementation and
review. See [PostgreSQL OAuth](https://www.postgresql.org/docs/18/auth-oauth.html)
and [validator modules](https://www.postgresql.org/docs/18/oauth-validators.html).

## Install

From the `gitops` checkout:

```sh
make plan
make up
```

The private infrastructure checkout keeps `postgres.tf` separate from `main.tf`.
Its `main.tf` configures the Calico policy engine; a network policy change can replace an existing AKS cluster.
If the plan replaces a cluster containing data, back up and tear down the old
platform before applying it. Review those settings against the existing cluster
before applying. Set `postgres_storage_account_name` in private tfvars if the
default (up to 22 characters of the LGTM account name plus `pg`) is unavailable.
ZRS must be available in the selected region. Provisioning requires Azure storage
and role-assignment permissions; newly granted data RBAC can take time to propagate.

Commit/push the GitOps changes to the branch Flux reads before startup.
Terraform creates Azure infrastructure first; `make up` reads `postgres_connection`
and creates the non-secret `postgres-settings` ConfigMap in `flux-system`.
The database phase requires that map and never falls back to an empty database
when it is missing. It waits for all database instances and the actual pooler
Deployment, then takes/verifies a base backup.

Three PostgreSQL pods require three distinct nodes. The existing AKS autoscaler
can provision them. Database requests total 1.5 CPU / 3 GiB, plus sidecars,
poolers and controllers. Each instance has a 32 GiB Premium SSD disk, no host
caching and delayed binding. See [Azure Disk CSI](https://learn.microsoft.com/en-us/azure/aks/create-volume-azure-disk).

## Scaling and clients

Change `Cluster.spec.instances` in Git for read replicas; reads can use
`platform-ro.postgres.svc.cluster.local`. Writes still use one primary; replicas
do not shard writes. Increase CPU/memory and disk capacity in Git. Disks can grow,
but cannot shrink. Required node anti-affinity protects against a node failure;
availability-zone isolation requires coordinating AKS zones and database topology.

One synchronous standby must acknowledge writes. Losing both standbys blocks
writes until one returns, trading availability for durability. A single-instance
dev variant must also remove the `synchronous` stanza; changing only `instances`
would block writes.

Scale PgBouncer independently. Two poolers each allow 500 clients and at most 80
server connections per database, within the configured 200-connection database
limit. Recalculate that budget before adding poolers. Transaction pooling requires
application compatibility with transaction boundaries/session state. See
[CloudNativePG pooling](https://cloudnative-pg.io/docs/1.30/connection_pooling/).

Connect to `platform-pooler.postgres.svc.cluster.local:5432`, database/user `app`.
Label the client namespace and pod template with
`gitops.platform/postgres-client: "true"`. Credentials are in `platform-app` and
the public CA is `platform-ca`'s `ca.crt`, both in `postgres`. Use Secret references,
mount only the public CA key, and set `sslmode=verify-full`. Cross-namespace apps
need secure distribution of their own credentials and CA; Kubernetes cannot mount
another namespace's Secret. Use separate SQL roles/databases for separate apps.

Recovery regenerates the application password and CA; CloudNativePG updates the
recovered `app` owner's password to match its new Secret. Consumers must reload
credentials/trust material. See [recovery application configuration](https://cloudnative-pg.io/docs/1.30/recovery/#configure-the-application-database).

## Teardown and recreation

`make down` suspends reconciliation, removes consumers and the backup schedule,
removes PgBouncer and waits for its pods, terminates old application sessions,
then waits for earlier backups and takes a final primary base backup. It creates
a named shutdown restore point and forces its WAL segment into the archive.
The command verifies the backup in Blob and uploads/reads back the recovery
marker before removing PostgreSQL/PVCs, Barman, CloudNativePG, cert-manager and AKS.

`lifecycle/platform.json` retains the archive server, backup ID, PostgreSQL major
and clean shutdown target. The next `make up` recovers into a new Cluster and
writes new backups to a fresh archive server. Repeated startup preserves existing
bootstrap/archive settings. Bad credentials/state, missing backups or archives
without a marker cause an error instead of empty initialization. Recovery is a
new-cluster bootstrap. See [CloudNativePG recovery](https://cloudnative-pg.io/docs/1.30/recovery/).

Backup account/container, identity and data roles survive `make down`; federation
is recreated against the new issuer. PVC disks use `Delete`: the verified external
backup is the persistence boundary. Backups have **no automatic expiry** so months
of inactivity do not invalidate recovery. Retained archives/versions incur storage
costs. Remove obsolete generations only after testing a newer restore, preserving
all WAL required by retained backups.

If teardown fails, AKS/PostgreSQL remain, but consumers may be gone and Flux paused.
Fix the backup/identity problem and retry teardown, or use `make up` to resume.
Do not delete volumes or recovery settings to get past a failure. Run lifecycle
commands serially and keep application traffic behind the pooler.

Unexpected AKS deletion restores to the latest archived WAL using the last verified
marker. `archive_timeout=60s` limits idle WAL delay but does not guarantee a 60-second
RPO during an outage. Restore time depends on backup size, WAL, disks and node
scaling; instantaneous startup and zero loss after unplanned deletion are not promised.

Manual uninstall must follow `scripts/cluster.py`'s backup/deletion order. Retain
the Blob marker and remove `postgres-settings` only after the Cluster is gone so
reinstallation selects a fresh archive. Helm can retain CRDs: delete them only
when no databases anywhere depend on them. The active Flux graph reinstalls these
packages when resumed.

For PostgreSQL-only pausing while AKS remains, stop clients then set
`cnpg.io/hibernation=on`. PVCs remain; hibernation does not take a backup.
`make up` sets it back to `off`. Use `make up` before full teardown of a hibernated
database. See [declarative hibernation](https://cloudnative-pg.io/docs/1.30/declarative_hibernation/).

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate.py
kubectl -n flux-system get kustomizations postgres-operator postgres
kubectl -n postgres get clusters,poolers,backups,scheduledbackups,pvc
kubectl -n cnpg-system get helmreleases,deployments
```

Before relying on recreation, insert a known row over TLS, run `make down` then
`make up`, verify the row and a new successful backup. Test unlabeled-client
denial, certificate verification, plaintext rejection, primary failure and client
reconnection. Kubernetes admins with pod exec access remain trusted and can use
the local PostgreSQL superuser socket. Local tests are not proof of a live restore.

Existing collectors ingest pod logs. PostgreSQL exposes metrics on 9187; database
scraping and backup-age alerts have not been wired into LGTM by this change.
