# LiteLLM internal AI gateway

The platform installs the official `litellm-helm` chart **1.109.1** and LiteLLM
**v1.109.1**, GitHub's designated latest stable release checked on 15 November
2026. The image is pinned by its multi-platform digest; no floating `latest`,
`main-stable` or development tag is used. This package uses the open-source
proxy; Enterprise JWT authentication and custom classifier features are not enabled.

## Configuration

```yaml
litellm:
  enabled: true
  modelList: [] # Additional approved native routes; Azure chat is configured below.
```

When `litellm.azure.enabled` is approved and enabled, the existing Azure `chat`
deployment is exposed through the same gateway alias,
using a dedicated ASO-managed provider identity and account-scoped OpenAI User
permission. Consumer identities remain separate. The proxy does not use the
hosting project's inference proxy. Azure `reasoning` and `coding` aliases use the same approved GPT deployment through
Responses; `embedding` uses the existing text-embedding-3-small deployment. No
Claude/Gemini activation remains disabled until the Google acceptance gates below pass.

Internal management remains at `http://litellm.litellm.svc.cluster.local:4000`.
When `litellm.access.enabled` is approved and enabled, an issuer-annotated ListenerSet attaches to the existing private Gateway for
`models.internal.<DNS zone>`; its HTTPRoute exposes only POST
`/v1/chat/completions`, `/v1/responses` and `/v1/embeddings`. Management, health and UI paths are not routed. No public
endpoint, firewall change or separately managed Certificate is introduced.
The configuration enables both activation flags. Source configuration
is not evidence that this route is deployed or reachable; verify reconciliation
and actual model transport after merge.

`litellm.access.policies` supplies trusted model allowlists and spend/rate ceilings.
Application intent selects a policy through the reusable application chart, which
owns finite credential creation, renewal and revocation. No per-app registration
or consumer credentials belong in this package. The reusable `documents` processing
tier permits chat only and gives each generated consumer its own USD5/30day budget,
12RPM,50000TPM and2concurrent requests. Any repository can select this tier; it is
not an application registration or shared consumer identity. Missing/non-positive
ceilings fail closed rather than becoming unlimited. Never
put API keys or provider credentials in Git-managed values.

The reusable `bounded-agent` policy separates default/coding/embedding keys under
one stable consumer owner. Its initial testing tier permits USD10/30days in
aggregate,20RPM,50000TPM and2concurrent requests per generated consumer. These are
operator-owned testing defaults, to be reviewed against observed usage. `litellm-consumer-defaults`
is public trusted delivery metadata, not a credential or an application registry.

Native user budgets/accounting use the existing PostgreSQL database and Redis.
Routing retries/fallbacks and client-provided provider credentials are disabled.
Payload storage in spend logs is disabled. Exact model pricing, gateway responses,
forbidden request overrides and accounting require live acceptance. A read-only
check of the existing pinned image found positive input/output pricing for the
selected model; this does not establish invoiced-cost reconciliation.

This milestone adds no backup/recovery service. Issuer control records in Key Vault
retain the stable owner and block reissuance if previously established accounting
is missing; this prevents silently granting a fresh allowance after DB loss. It
does not prove that old keys retained by a caller are rejected by a newly empty DB,
or provide accounting restoration. A full teardown/rebuild must remain blocked for
consumer admission until budgets/accounting are reconciled. Shared project model
permissions remain unchanged until SRE also validates its gateway path.

## Dependencies and ownership

| Wave | Owner | Resources |
| --- | --- | --- |
| -10 | namespaces | LiteLLM namespace |
| 5 | litellm-reloader | Existing upstream namespace-scoped Reloader chart |
| 20 | litellm-key-vault | ASO writer identity/trust/grant, ESO create-if-missing vault entries |
| 20 | redis-certificates | Dedicated `litellm` Redis client certificate |
| 30 | postgres | CNPG database, role and PostgreSQL client certificate |
| 30 | redis | Dedicated certificate-to-ACL user limited to `litellm:*` |
| 35 | litellm-credentials | Runtime ServiceAccount; read-only ESO delivery of vault keys, PostgreSQL leaf and public server CA |
| 40 | litellm | Upstream proxy and PreSync Prisma migration Job |

The credentials Application creates the token-unmounted `litellm` ServiceAccount
before the upstream Application starts. Both migration and proxy reuse it. A
ServiceAccount created in the upstream chart's normal Sync phase cannot satisfy
its PreSync migration on a fresh install. The upstream chart therefore has
`serviceAccount.create: false`; its migration hook and deadline remain enabled.

The bundled PostgreSQL and Redis subcharts are disabled. The proxy and migration
Job connect directly to the existing CNPG primary with the same dedicated
`litellm` database owner; that role has no superuser, database-creation,
role-creation or replication privileges. Native Prisma owns only its application
schema; CNPG retains SQL role/database ownership. Atlas reconciles neither.
The certificate-only URL is supplied through native environment values because
the chart's `db.useExisting` helper otherwise injects a password Secret reference.

Prisma receives the leaf as PKCS#12 plus the public server CA, using strict
certificate validation. Native migration coordination and index checks use
psycopg/libpq, which receives the same leaf's PEM certificate/key through
`PGSSLCERT`/`PGSSLKEY`. LiteLLM converts the Prisma CA/strict URL settings to
libpq's `sslrootcert`/`verify-full`; libpq cannot use Prisma's `sslidentity`.
The existing ExternalSecret delivers both representations in the same read-only
mount before migration. Renewal updates that Secret and the existing Reloader
integration restarts the proxy; consumers are removed before credentials on teardown.
The public `changeit` wrapping value is not a PostgreSQL
password; Kubernetes RBAC protects the private key. The Redis client verifies the
server hostname/CA and supplies its distinct PEM client leaf. Redis maps its CN
to the `litellm` ACL user, without a Redis password or access to Langfuse keys.
Only this leaf is reflected into the exact `^litellm$` namespace. Neither database
nor Redis CA signing keys are distributed. This database/cache wiring needs no
cloud-provider identity; the separate vault writer described below is used only
for initial key creation.

## Key Vault source and initial creation

The existing Key Vault selected by `runtime.AZURE_KEY_VAULT_URI` is authoritative
for the stable master key and encryption salt. Their names are
`platform-<cluster.name>-litellm-master` and `platform-<cluster.name>-litellm-salt`.
Different clusters do not share these identities. The package reuses the existing
vault, ESO, ASO and Reflector; it creates no vault, database server or cache service.
The shared `azure-kv` ClusterSecretStore and its read-only identity are unchanged.

At wave 20, native ESO Password generators and PushSecrets create entries only
if absent (`updatePolicy: IfNotExists`, as supported by pinned ESO 2.12.0).
`deletionPolicy: None` keeps the vault entries when the package/cluster is removed.
The generated candidates are never the application's source: at wave 35 an
ExternalSecret fetches the actual vault values through `azure-kv`, so existing
entries win on reapply or a rebuild. No Helm randomness, setup Job, imperative
secret creation or manual seeding is required.

ASO creates one dedicated `id-<cluster.name>-litellm-key-writer` managed identity in
the existing cluster resource group, federated only to
`system:serviceaccount:litellm:litellm-key-writer`. Its **Key Vault Secrets Officer**
role is scoped to the existing vault. This built-in role can manage all secrets
in that vault; it is not restricted to the two entry names. Write authority is
kept off the shared platform/ESO identity and off the LiteLLM pod. The writer's
namespaced SecretStore is used only by the two PushSecrets. Only its public client
ID is exported by ASO and reflected to `litellm`; no client secret is created.
ESO's native `authSecretRef.clientId` resolves the ASO output without a bootstrap
handoff. `keyVault.resourceGroup` and `identityLocation` are platform metadata;
identity names, trust, assignment GUID and entry names are template-owned.

The master key administers virtual keys; the salt protects stored credentials.
The pod still needs an immutable Kubernetes Secret, populated once from Key Vault.
Remote value changes deliberately do not rotate this running copy automatically.
Rotation is a coordinated operation, particularly for the salt against a populated
database. Follow upstream encryption migration guidance before replacing it.

Both client leaves last 30 days, renew seven days before expiry, and rotate keys.
ESO/Reflector deliver renewed leaves, then the namespace-scoped Reloader restarts
the proxy. Argo ignores only the Reloader rollout marker. Rotation scheduling is
configured; live renewal/reconnection is a separate acceptance test. CA trust
rollover and immediate revocation are not provided by leaf renewal.

Defaults are one replica/worker, 1 CPU and 4Gi memory requested, a 4Gi memory limit,
and five Prisma connections. The migration Job requests 100m/1Gi, is capped at
2Gi, and has a 600-second deadline. Its original 1Gi cap caused observed OOM kills
after schema/index setup; the higher cap retains the native entrypoint and its
Prisma client generation. Proxy sizing follows the upstream DB-connected worker
memory guidance; no nodes, HA, additional stores or autoscaling are provisioned.
Confirm available capacity before deployment. The official image retains its
writable filesystem/default user; capabilities are dropped, privilege escalation
is disabled, the seccomp profile is RuntimeDefault and the ServiceAccount does
not mount an API token. The chart's standard migration container is used unchanged.

Teardown removes the proxy before credentials, backing services and controllers.
The writer identity, federation and role grant are removed after the consumers
and credentials; the vault itself is never owned or deleted. The database uses
the existing `retain` reclaim convention; disabling the package
is not a promise of complete database deletion or a data-preserving pause. The
platform remains disposable at full cluster teardown. ESO orphan keys remain
until their namespace is deleted. No Terraform backend resource is affected.

## Validation and provenance

`make validate` checks enabled/disabled rendering, distinct SQL/Redis identities,
secret references, create-if-missing/retain policies, cluster-isolated vault entry
names, writer trust/role, certificate delivery, dependency waves, migration configuration,
and the exact upstream deployment image. The upstream chart is rendered twice to
check that it does not generate credentials during reconciliation.

Verified during preparation:

- Chart: `ghcr.io/berriai/litellm-helm:1.109.1`, OCI manifest digest
  `sha256:4e60ab64be8f54232f9b3dd81ad9d66a4b9439bf5983d276f0f9617564355553`.
- Image: `ghcr.io/berriai/litellm:v1.109.1`, multi-platform digest
  `sha256:11175975a3093e05e92faee2fe2fe3412a3ae2fcaa849eb0fe25d5f0d8bcb2e9`.
- Both signatures validated with cosign using the upstream release public key
  from commit `e82a3c646908ba0a6ee0beddc4310d7a6cac4065`, including offline transparency-log inclusion verification. Chart version is
  pinned in Argo; its OCI digest is recorded here, not enforced by Argo at sync.

These are source/render checks, not deployment or inference evidence. After merge
and authorised deployment, check current-revision Argo health, migration completion,
proxy readiness, first-time vault seeding and existing-value preservation, database/Redis
authentication and certificate renewal. The writer role propagation and vault access
remain live gates. Then test
provider-specific streaming, tools and Responses compatibility when onboarding
models. A healthy gateway with an empty model list proves no provider integration.

Sources: [stable release](https://github.com/BerriAI/litellm/releases/tag/v1.109.1),
[versioned upstream chart](https://github.com/BerriAI/litellm/tree/v1.109.1/helm/litellm-helm),
[CNPG client certificates](https://cloudnative-pg.io/documentation/current/certificates/),
[LiteLLM coordination Redis](https://docs.litellm.ai/docs/proxy/caching),
[Enterprise feature boundary](https://docs.litellm.ai/docs/enterprise),
[ESO PushSecret policies/generators](https://external-secrets.io/latest/guides/pushsecrets/),
[ESO Key Vault workload-identity references](https://external-secrets.io/latest/provider/azure-key-vault/),
[ASO identity output expressions](https://azure.github.io/azure-service-operator/guide/secrets/).


## Foundry GPT, Bedrock Claude and Agent Platform Gemini

The Azure GPT and embedding aliases retain their existing France DataZone
upstream. `claude` uses `bedrock/converse/eu.anthropic.claude-sonnet-5-5` from
Paris (`eu-west-3`); `gemini` uses `vertex_ai/gemini-3.8-flash` with location `eu`
and `https://aiplatform.eu.rep.googleapis.com`. Claude is not routed through GCP.
The pinned SDK contains the EU Sonnet price/capability metadata. Global/provider
fallback lists stay empty; model failures do not change the processing boundary.

The provider enable flags express desired availability. Bedrock additionally
requires `runtime.AWS_GATEWAY_ROLE_ARN`; Gemini requires all four
`GOOGLE_GATEWAY_*` references. Missing metadata omits that provider's model,
credentials/mounts and admission policies, preserving Azure-only reconciliation
before foundation apply/publication. This is not a promise of successful inference.

Bedrock uses native AWS web identity credentials: a projected one-hour token for
`sts.amazonaws.com`, the role ARN, Paris AWS Region and regional STS. The role
trusts the exact AKS issuer and `system:serviceaccount:litellm:litellm` subject.
The runtime role may invoke only the reviewed EU profile and its six destination
models through that profile. No access key, exchange helper, subscription
permission or additional Kubernetes identity is added.

Gemini keeps the existing native external-account ConfigMap and short-lived token.
`vertex_credentials` is scoped to that model, without a global ADC override.
Required outbound HTTPS hosts are `bedrock-runtime.eu-west-3.amazonaws.com`,
`sts.eu-west-3.amazonaws.com`, `aiplatform.eu.rep.googleapis.com`,
`sts.googleapis.com` and `iamcredentials.googleapis.com`. Connectivity is a live
acceptance gate; this change includes no network modification.

Consumer intent remains one policy:

```yaml
gatewayAccess: {policy: bounded-reasoning-coding}
```

That policy maps default to Azure reasoning, coding to Bedrock Claude and
embedding to Azure. `bounded-claude` and `bounded-gemini` remain provider-specific
options; `bounded-agent` remains the Azure baseline. Required routes gate policy
publication. These policies share the same testing tier: USD10/30days,20RPM,
50000TPM,2parallel per stable consumer across capability keys. Policy changes keep
the existing owner/accounting; they do not grant a fresh budget. The document tier
and running demo remain unchanged. Gemini availability adds no SRE workflow stage.

Known cost limitation: the pinned Gemini price map uses global prices, while the
reviewed EU rate is10% higher. This is recorded at the user's direction; do not
represent the proxy allowance as an exact provider-bill ceiling. Recheck Gemini's
introductory rates before2027-01-01. Bedrock EU pricing, reasoning/cache usage and
actual owner attribution also need readback during acceptance.

The existing CI publisher can read only `litellm-consumer-defaults` through its
metadata Role. Its separate registrar Role grants get/list/watch/create/patch on
Argo Applications in `argocd`, which is explicitly privileged platform deployment
authority. Native RBAC does not restrict Application spec fields or name prefixes.
Neither Role grants Secret reads, deletion or wildcard resources.

After merge/apply, verify token exchange/renewal and denial for other subjects,
audiences and global/US/direct model paths; then test Responses, tools, schema,
streaming, key/route/override restrictions and accounting with isolated allowances.
Before the first Bedrock call, obtain the required model-term approval: invocation
can start a subscription and accept the EULA. Source/CI success establishes no
entitlement, live inference or consumer promotion.

Sources: [AWS inference-profile permissions](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-prereq.html),
[AWS web identity](https://docs.aws.amazon.com/sdkref/latest/guide/access-assume-role-web.html),
[Sonnet EU destinations](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html),
[Bedrock model access and terms](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html).
