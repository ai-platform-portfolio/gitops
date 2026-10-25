# DevLake delivery analytics

Free, self-hosted Apache DevLake **v1.0.2**, with digest-pinned core and
Config UI images. Management only, enabled by default. Reuses the existing CNPG
PostgreSQL 18 cluster and Grafana. Adds no MySQL, PostgreSQL server, Grafana,
Redis, queue service, PVC, Azure resource, Terraform input or bootstrap handoff.

`make up` installs the complete package and waits for its configuration Job.
Existing clusters reconcile a merged GitOps change automatically. No manual installation or secret-generation command is needed. Service projects
are configured in GitOps; source/incident integrations still need real inputs. The administration URL
is `https://devlake.<runtime.DNS_ZONE>`; dashboards are in Grafana's **Delivery**
folder. An empty dashboard is expected until real service data is configured.

The existing Atlas admission fix [PR43](https://github.com/ai-platform-portfolio/gitops/pull/43)
is a separate full-platform prerequisite on the current main branch. This package
does not duplicate that fix or imply a successful live bootstrap.

Related manifests are grouped by deployment stage: `configuration.yaml` holds
generated secrets and network policy; `credentials.yaml` holds database/Grafana
credential delivery, dashboards and configuration access; `application.yaml` holds
the UI configuration and workloads; `collection.yaml` holds collection and SSO.
Namespace, identity, database, configuration Job and public networking remain
separate where they join independently ordered existing stages.

## Ownership and sequencing

| Wave | Existing/new stage | Readiness dependency |
| --- | --- | --- |
| -10 | namespaces | DevLake namespace with restricted admission |
| 0, 5, 6 | existing Crossplane, provider and identity API | Entra APIs available |
| 5 | namespace-scoped DevLake Reloader | Same pinned chart/pattern as existing services |
| 7 | existing service-identities | Crossplane OIDCApplication, principal/password and explicit assignments |
| 10 | existing configuration | ESO generates stable database/encryption/administration credentials |
| 30 | existing postgres | CNPG reconciles two limited roles and the dedicated database |
| 40 | devlake-credentials | Scoped ESO delivery of database passwords and CA; dashboard ConfigMap and outbox objects |
| 45 | existing Grafana | Datasource credentials and CA now exist; avoids a wave-30 startup deadlock |
| 60 | devlake | Core migrations complete (`/ready`), UI ready |
| 70 | devlake-configure | Owner grants dashboard SELECT, then authenticated project/webhook/key reconciliation |
| 75 | devlake-collection | SSO policy/filter installed before public routes; collection after ingestion key |
| 80 | existing routes | ListenerSet/HTTPRoutes, followed by a Job checking current OIDC acceptance and certificate readiness |

The existing Application health customization gates parent waves. The configuration
Job is a normal tracked Job with a content-derived name; a failed Job blocks
completion. Reverse-wave teardown stops collection and clients before PostgreSQL,
and removes Entra requests while Crossplane remains available. No finalizers are
force removed. Application data, pending delivery events and keys are disposable
with this portfolio's teardown; Terraform state remains separate.

There are no ARM resources to reconcile for this package, so ASO needs no changes.
Entra objects have one owner: the existing Crossplane AzureAD provider/composition.
No new identity controller or Terraform registration is added. The existing Envoy
Gateway supplies the SSO overlay described below.

## Stable version and identity boundary

As checked on 2026-10-25, **v1.0.2 is the newest published non-beta release**.
GitHub labels v1.0.3-beta18 “Latest” despite its beta name; it is not selected.
The v1.0.3 tag without a published release is not used either. Both images are
pinned to official v1.0.2 digests; there is no patched image or source fork.

Stable v1.0.2 has PostgreSQL support but its unrelated AWS Q Developer plugin
contains a MySQL-only migration. The core command selects the official, unchanged
`dora`, `webhook`, `org`, `refdiff`, `github`, `github_graphql` and `gitextractor`
plugins using `PLUGIN_DIR`. Symlinks in the existing /tmp emptyDir avoid copying
hundreds of MiB of plugins or adding another init container. Remote Python plugins
are disabled. Other connectors are intentionally unavailable until their native
PostgreSQL migrations are reviewed. Do not restore automatic loading of every
plugin: it fails PostgreSQL startup on this stable release.

**Stable DevLake has no native OIDC user authentication.** Crossplane owns its
Entra application/principal/password and explicit administrator assignments.
The OIDC callback is `https://devlake.<zone>/oauth2/callback`. Envoy's existing
SecurityPolicy authenticates those assigned users. After authentication, an
HTTPRouteFilter injects a generated Basic credential into the administration
route. Nginx **retains and verifies** Basic authentication using ESO's matching
htpasswd file; local application authentication is not disabled.

This supplies a gateway session and a shared application administration identity,
not per-user DevLake sessions or granular application RBAC. All `devlake.users`
are trusted administrators. Grafana keeps its independent native Entra login and
reader access. Entra audit/access logs establish gateway user identity; DevLake
cannot supply equivalent per-user authorization/audit records in this release.

Only the UI has a public backend route. The unauthenticated core is private and
NetworkPolicy permits port 8080 only from the UI, configure Job and collector pod
labels within the namespace. Untrusted users must not have pod/Secret write access
there. The UI strips identity headers, blocks migration confirmation, and requires
the expected Origin for unsafe administration API methods. Internal HTTP is
restricted by policies but is not encrypted. Public access uses HTTPS.

The more specific `/api/rest/plugins/webhook/` machine route has **neither OIDC
redirects nor injected administrator credentials**. It retains DevLake's native
scoped Bearer authentication. The configure Job creates that key in
`devlake/devlake-ingestion`, with scope restricted to each configured project's
`deployments` and `issues` paths. Only this exact Secret's data has an Argo drift
exception. Do not print the key or put it in Git. Project-set changes rotate it;
ordinary reruns reuse it. Consumers need an approved credential-delivery path.

Entra credentials have explicit 180-day validity; automatic time-based rotation
is not introduced. The namespace-scoped Reloader restarts core/UI when their
consumed secrets change. Envoy observes its OIDC/credential secrets directly.
A Grafana password/CA change requires a Grafana restart because its datasource
provisioning is read at startup.

## Database permissions and future Atlas work

CNPG owns database `devlake`, owner role `devlake` and reader `devlake_grafana`.
Both roles have LOGIN but no SUPERUSER, CREATEDB, CREATEROLE or REPLICATION.
`pg_hba` permits each only on the dedicated database and rejects non-TLS clients.
DevLake connects directly to `platform-rw.postgres.svc.cluster.local` with
`sslmode=verify-full` and the CNPG CA; direct connections avoid transaction-pooler
interference with migrations/session semantics. Grafana also verifies the CA and
hostname, uses its separate reader and caps connections at five.

The owner runs DevLake's native migrations within its own database/public schema.
`FORCE_MIGRATION=true` makes first deployment automatic; pinned-version upgrades
can also execute migrations, so review upstream migration changes before upgrades.
A Helm rollback does not undo a database migration. Backups use the existing CNPG
policy. Preserve the generated encryption Secret while keeping its database:
connection credentials in DevLake are encrypted with it.

The configuration Job grants CONNECT, public-schema USAGE and SELECT only on the
six domain tables used by these dashboards. It grants no write access,
internal API-key/connection-table access or blanket future-table access. New
dashboard tables need an explicit grant review. No CREATE EXTENSION or
maintenance-database privileges were needed. The unmodified stable image completed a fresh PostgreSQL 18 migration with hostname-verified TLS
and only the selected plugins.

For the later Atlas transition, inventory these role/database ownership and SELECT
grants separately from the application. CNPG and this Job retain ownership now;
DevLake retains schema migration ownership. Do not let Atlas reconcile the same
objects concurrently or adopt DevLake's migration history without a separate design.

## Service projects and release provenance

No production workload or incident system is identified in the current repository.
`devlake.projects: {}` intentionally creates no synthetic production history.
A configured project automatically gets its own webhook, DORA metric settings and
five-minute calculation blueprint. Use one project per service/environment so
unrelated incidents cannot be attached to a neighbouring service's last deployment.

For example, after a real leaf Application exists:

```yaml
devlake:
  projects:
    inference-production:
      description: Production inference service
      application: inference-api
      environment: PRODUCTION
```

`application` is optional for projects receiving deployments from another producer.
When present, it names a **local, single-source leaf Application in argocd**;
collection RBAC grants GET only on those exact names. Do not point it at a root,
onboarding, credentials, route or configuration Application. The current package
collects on management only; it does not pretend to collect independent workload
clusters. Workload/fleet onboarding requires credential delivery and a local producer
per cluster, and is future work rather than an implicit central cluster-admin token.

Put immutable provenance in that leaf's **source values**, so Argo retains it in
the reconciled source snapshot:

```yaml
spec:
  source:
    helm:
      valuesObject:
        delivery:
          id: inference-release-42
          repository: https://github.com/example/inference.git
          commit: 0123456789abcdef0123456789abcdef01234567
          purpose: planned
```

The chart must tolerate the extra `delivery` values (the existing resources chart
does). `commit` is the actual built source SHA, not a chart version, image digest or
GitOps promotion SHA. Update provenance in the same reviewed promotion as the image.
`id` identifies a promotion; keep it unchanged for resync/self-heal and assign a new
ID for a real rollback/redeployment. For incident remediation, set `purpose:
remediation` and `incidentId: <incident-id>`. The added rework panel counts those
explicitly classified successful production deployments.

With no projects, the CronJob is suspended and creates no idle pods. When projects
are present, the collector runs once per minute and requires current-source match, Synced,
Healthy and a successful operation. It never infers production from names and
rejects missing/invalid provenance. Multi-source Applications require a deliberate
adapter; the collector does not guess which revision belongs to application code.
The deployment completion timestamp is the first healthy observation, with up to
one polling interval of measurement resolution, not CI publication time.

Before sending, it writes the immutable payload to a ConfigMap outbox. Pending
records survive pod/API outages and are retried with the same deployment ID.
DevLake upserts those IDs. A crash after acceptance but before acknowledgment
therefore replays the same record. Only the outbox ConfigMap data has an explicit
Argo drift exception. The collector has no Secret-list, cluster-admin or Argo
server credential access. The buffer fails loudly above 800 KiB rather than losing
pending records; this is a compact starting implementation, not a fleet event broker.

**Coverage limit:** a release overwritten between polls, or while the collector
cannot observe Kubernetes, can be missed before it enters the outbox. Historical
Argo status cannot prove user-visible readiness. This implementation does not claim
lossless fleet collection, production canary exposure tracking or automatic incident
causality. Monitor failed CronJobs and backlog; a production rollout pipeline requiring
lossless capture needs durable producer-side events before expanding this collector.

## Incidents and source changes

The authenticated native endpoints are:

- `/api/rest/plugins/webhook/connections/by-name/<project>/deployments`
- `/api/rest/plugins/webhook/connections/by-name/<project>/issues`

Use `Authorization: Bearer <ingestion-token>` over HTTPS. Native payload schemas
are pinned in the upstream [webhook implementation](https://github.com/apache/devlake/tree/v1.0.2/backend/plugins/webhook/api).
An incident needs `issueKey`, `title`, `type: INCIDENT`, `status`, `originalStatus`,
`createdDate` and, on recovery, the real `resolutionDate`. Send updates with the
same issue key. Lead time requires source commits collected by the enabled native GitHub connector.
Configure that connection with a dedicated GitHub App/token and associate its repo
with the service project. No unrequested PAT is created or borrowed from another
service, and stable v1.0.2 does not have the newer PR webhook API. Until the source
is connected, the dashboard shows missing source coverage and no lead-time sample.
There is no configured production workload, GitHub ingestion credential or incident
provider in the current package defaults, so no real production metrics are claimed.

Use the same project for service incidents and reconcile updates/recoveries. Do not
fabricate incidents from failed CI jobs or Linear issue completion timestamps.

DevLake's incident relationship calculation uses service-scoped temporal matching.
Review/correct attribution; time proximity does not establish causality. Its bundled
recovery calculation starts at deployment completion, while general MTTR measures
incident duration. The compact dashboard uses native PostgreSQL SQL, counts distinct deployments,
shows a source-commit lead-time sample/coverage, deployment-linked failures/recovery,
explicit remediation rate and open incidents. It adds no fixed industry tiers.
Source lead time covers the deployed SHA rather than every intermediate commit
in a release batch. Do not interpret zero/no data as evidence that no failures occurred,
or use these measurements to rank individual developers.

## Validation and sources

Validation covers management-only and disabled-package rendering, dependency gates,
restricted pod specifications, narrow roles, deferred ESO template expressions,
source/environment attribution, no-op deduplication, configuration idempotency and
all pinned upstream Helm renders. Disposable runtime checks cover the unmodified
stable images, non-superuser PostgreSQL 18 migration with verified TLS, deployment
replay, scoped Bearer ingestion, incident association/DORA calculation, eight
PostgreSQL dashboard queries and denial of credential-table/write access for the
Grafana reader. The pinned Envoy Gateway was tested for OIDC redirects, rejection
of a Basic-auth bypass, separate machine authentication, and closed failure when
an OIDC policy is invalid. This is not proof of real Entra consent/assignments,
interactive login, Azure NetworkPolicy enforcement, AKS bootstrap or fleet scale.

Sources: [stable release](https://github.com/apache/devlake/releases/tag/v1.0.2),
[stable plugin loader](https://github.com/apache/devlake/blob/v1.0.2/backend/core/runner/loader.go),
[stable Q Developer migration](https://github.com/apache/devlake/blob/v1.0.2/backend/plugins/q_dev/models/migrationscripts/20250320_modify_file_meta.go),
[Envoy OIDC](https://gateway.envoyproxy.io/docs/tasks/security/oidc/),
[credential injection](https://gateway.envoyproxy.io/docs/tasks/security/credential-injection/).
