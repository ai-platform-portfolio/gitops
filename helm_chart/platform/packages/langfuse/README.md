# LLM observability

[The Application](../../templates/langfuse.yaml) installs Langfuse at wave 40.
`values.yaml` disables bundled PostgreSQL and connects directly to the shared CNPG
primary. The postgres Application (wave 30) owns the dedicated `langfuse` database,
restricted login role and client Certificate. The credential Application (wave 35)
delivers the client keystore/server CA and generates the telemetry bootstrap
credential before Langfuse starts at wave 40.
The database participates in the existing CNPG physical backup configuration;
restore testing of this database is separate acceptance work.

Authentication uses the `langfuse` client certificate with a 30-day lifetime,
renewal seven days before expiry and a new private key on renewal. cert-manager
issues it in `postgres`; scoped ESO access copies its PKCS#12 keystore and the
server CA into `langfuse`. The signing CA private key stays in `postgres`.
Reloader restarts both web and worker when the delivered Secret changes.
Prisma verifies the server certificate and hostname (`sslaccept=strict`).

Prisma requires a PKCS#12 identity and cert-manager requires a non-empty keystore
password. `changeit` is an explicitly public format-wrapping value, not a database
credential or a security boundary. Kubernetes Secret RBAC protects the client
private key; CNPG accepts the dedicated certificate identity, with no database
password or superuser access. No new long-lived authentication secret is added.

Redis, ClickHouse and S3-compatible storage remain bundled and disposable.
`values.yaml` references their existing cluster-local Secrets, plus Langfuse's
application encryption/session keys. Those stores are separate from shared CNPG
and are not covered by its backups. Existing Secrets are reused within a running
cluster. Switching this unused installation requires no data migration; the old
bundled PostgreSQL PVC may be retained by its chart and can be removed during
normal disposable teardown.

`networking.yaml` contains the application-owned HTTPS ListenerSet, route and
redirect. The routes Application applies these after backend readiness;
cert-manager issues a per-host certificate.

## Native Microsoft sign-in

On management, `langfuse.sso` in platform values enables the native Entra OIDC
provider. `disableUsernamePassword` remains false for recovery until the profile
email prerequisite and interactive Entra login are verified; set it true afterwards. The callback is
`https://langfuse.<DNS_ZONE>/api/auth/callback/azure-ad`. Workload clusters do not
install the management identity controllers or inherit this identity; their
Langfuse password login stays enabled until a child-specific SSO design is added.

`identity.yaml` requests a Crossplane `OIDCApplication`. Its composition owns the
single-tenant registration, enterprise principal and credential. Direct assignments
come from the `users` map: keys describe users and values are their Entra **object
IDs**, not email addresses. Removing an entry removes that managed assignment.
Direct user assignments do not require premium group-assignment licensing. The
principal requires assignments, so other tenant users are not granted access by
virtue of sharing the tenant. Out-of-band assignments must not be added: Crossplane
manages the declared assignment resources, not an authoritative inventory of all
assignments on the principal.

The provider requests an email ID-token claim, but that does not create a missing
Entra profile email. Every assigned user needs a usable `mail` attribute. The
current portfolio user's attribute was empty during research; updating that profile
requires the user's explicit approval and remains a deployment prerequisite.
The controller has no permission to edit user profiles.

The composition delivers `langfuse-sso` directly as a Kubernetes Secret with
`client-id`, `client-secret` and `tenant-id`. No lifecycle Graph script, Key Vault
copy, ESO bridge or extra identity setup command is used. Argo's wave/health gates
install the controllers and reconcile the identity before Langfuse starts. Later
assignment changes reconcile from GitOps without another `make up`.

The credential expires after `credentialValidityDays`. A scoped Reloader restarts
the web Deployment when its SSO Secret changes; automatic time-based rotation is
not configured. See the [controller package](../crossplane/README.md) for ownership,
permissions, credential expiry and teardown behavior. The generated registration
is disposable with this cluster; it is not a retained application credential.

Account linking is enabled only for this single-tenant, assignment-required
provider. Verify the email matches the existing Langfuse account. Entra sign-in
does not grant organisation/project membership or administrator privileges;
Langfuse retains those authorisation decisions.

After deployment, verify `/api/auth/providers` advertises Azure AD, sign-in redirects
to Microsoft, the intended local password recovery setting is respected, the assigned user reaches the
intended organisation, and an unassigned user is rejected. SDK/API-key ingestion
keeps its native authentication; no interactive Envoy login gate is added to it.

Sources: [Langfuse authentication](https://langfuse.com/self-hosting/security/authentication-and-sso),
[pinned implementation](https://github.com/langfuse/langfuse/blob/v4.50.0/web/src/server/auth.ts).

## Automated telemetry bootstrap

Chart 2.2.3 runs web/worker 4.76.0. ClickHouse and Keeper remain at 26.4;
Redis/Valkey and SeaweedFS keep their existing chart dependency versions. The AI
gateway remains disabled. Langfuse performs its database migrations on startup;
review migration health before accepting a live upgrade or attempting rollback.

Declare `langfuse.bootstrap.organization` and `langfuse.bootstrap.project` in
platform values (defaults: `platform` / `agents`). ESO generates one random
project credential in `langfuse-otel`. Its CreatedOnce, immutable, Orphan target
survives reconciliation and ExternalSecret recreation. Native `LANGFUSE_INIT_*`
creates the organisation, project and API key if missing, without an Enterprise
licence, bootstrap user/password, Key Vault copy or per-rebuild CLI command.
A full disposable teardown removes both database and credentials; the next
cluster initializes fresh credentials. This is bootstrap, not online key rotation.

Keep this Secret with the database. Deleting only the Secret beside retained data
is unsupported recovery: it can mint another API key and leave the previous key
valid. Restoring a database requires its matching application keys and bootstrap
Secret. Do not change organisation/project identifiers to rename existing data;
native initialization creates missing resources rather than migrating ownership.

Reflector delivers the credential only to `opentelemetry`; the collector reads
its Basic authorization header from a Secret reference. Keys never appear in
Helm values, Git or application instrumentation. Native Entra browser SSO and
existing organisation/project membership remain separate and unchanged; bootstrap
does not grant an existing user membership in the new project.

Send OTLP/HTTP traces to
`http://opentelemetry-collector.opentelemetry.svc.cluster.local:4318/v1/traces`.
Set the **string** resource attribute `langfuse.export=true` to forward all spans
in an AI investigation, or set it on selected spans only. Without this opt-in,
spans reach Tempo only. The additional collector pipeline filters before batching
and exports OTLP/HTTP to Langfuse with the v4 ingestion header. Logs and metrics
continue to Loki and Mimir. Both backends receive the same trace/span identifiers;
use the same incident identifier in application telemetry for correlation. Avoid
putting secrets or unnecessary incident payloads in span attributes.

There is no agent runtime in this repository. Instrumentation and evaluations
remain consumer work. After merge/deployment, verify one opted-in trace reaches
both backends, an unmarked trace reaches only Tempo, keys survive restarts, and
SSO users have the intended project membership. A full cold-cycle is separate
acceptance; render tests do not prove ingestion, migration or rebuild success.

Sources: [headless initialization](https://langfuse.com/self-hosting/administration/headless-initialization),
[OTLP ingestion](https://langfuse.com/integrations/native/opentelemetry),
[ESO lifecycle](https://external-secrets.io/latest/api/externalsecret/).
