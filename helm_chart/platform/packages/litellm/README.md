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
  modelList: []
```

The package installs on each cluster using its existing local platform services.
`modelList` maps to LiteLLM's native `model_list`. Empty means there are no usable
model routes yet. Provider credentials, Foundry model grants, caller access,
external exposure and task-based model selection require separate configuration
and acceptance. Never put API keys or provider secrets in Git-managed model values.
Applications later consume the gateway endpoint; they do not install this package.

The initial endpoint is `http://litellm.litellm.svc.cluster.local:4000`. The upstream
Service is ClusterIP, with no Ingress, HTTPRoute, ListenerSet, DNS or Envoy policy.
For local inspection, use `kubectl -n litellm port-forward svc/litellm 4000:4000`.
The API is protected by LiteLLM's master/virtual keys. ClusterIP is not network
isolation, and this installation supplies neither an Entra login nor trusted
identity propagation. Keep health endpoints and the admin interface internal.

## Dependencies and ownership

| Wave | Owner | Resources |
| --- | --- | --- |
| -10 | namespaces | LiteLLM namespace |
| 5 | litellm-reloader | Existing upstream namespace-scoped Reloader chart |
| 10 | configuration | ESO master-key/salt generators and stable output Secret |
| 20 | redis-certificates | Dedicated `litellm` Redis client certificate |
| 30 | postgres | CNPG database, role and PostgreSQL client certificate |
| 30 | redis | Dedicated certificate-to-ACL user limited to `litellm:*` |
| 35 | litellm-credentials | Named read-only ESO access to its PostgreSQL leaf and public server CA |
| 40 | litellm | Upstream proxy and PreSync Prisma migration Job |

The bundled PostgreSQL and Redis subcharts are disabled. The proxy and migration
Job connect directly to the existing CNPG primary with the same dedicated
`litellm` database owner; that role has no superuser, database-creation,
role-creation or replication privileges. Native Prisma owns only its application
schema; CNPG retains SQL role/database ownership. Atlas reconciles neither.
The certificate-only URL is supplied through native environment values because
the chart's `db.useExisting` helper otherwise injects a password Secret reference.

Prisma receives the leaf as PKCS#12 plus the public server CA, using strict
certificate validation. The public `changeit` wrapping value is not a PostgreSQL
password; Kubernetes RBAC protects the private key. The Redis client verifies the
server hostname/CA and supplies its distinct PEM client leaf. Redis maps its CN
to the `litellm` ACL user, without a Redis password or access to Langfuse keys.
Only this leaf is reflected into the exact `^litellm$` namespace. Neither database
nor Redis CA signing keys are distributed. No Azure identity is needed until a
provider is configured.

The stable master key and salt are necessary LiteLLM application credentials:
virtual-key administration uses the master key and stored credential encryption
uses the salt. ESO creates these once as an immutable orphan Secret. Re-rendering
or syncing does not rotate them. Rotation is a separate coordinated operation;
do not replace the salt against an existing populated database without following
the upstream encryption migration process.

Both client leaves last 30 days, renew seven days before expiry, and rotate keys.
ESO/Reflector deliver renewed leaves, then the namespace-scoped Reloader restarts
the proxy. Argo ignores only the Reloader rollout marker. Rotation scheduling is
configured; live renewal/reconnection is a separate acceptance test. CA trust
rollover and immediate revocation are not provided by leaf renewal.

Defaults are one replica/worker, 1 CPU and 4Gi memory requested, a 4Gi memory limit,
and five Prisma connections. The migration Job requests 100m/512Mi, is capped at
1Gi, and has a 600-second deadline. These follow the upstream DB-connected worker
memory guidance; no nodes, HA, additional stores or autoscaling are provisioned.
Confirm available capacity before deployment. The official image retains its
writable filesystem/default user; capabilities are dropped, privilege escalation
is disabled, the seccomp profile is RuntimeDefault and the ServiceAccount does
not mount an API token. The chart's standard migration container is used unchanged.

Teardown removes the proxy before credentials, backing services and controllers.
The database uses the existing `retain` reclaim convention; disabling the package
is not a promise of complete database deletion or a data-preserving pause. The
platform remains disposable at full cluster teardown. ESO orphan keys remain
until their namespace is deleted. No Terraform backend resource is affected.

## Validation and provenance

`make validate` checks enabled/disabled rendering, distinct SQL/Redis identities,
secret references, certificate delivery, dependency waves, migration configuration,
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
proxy readiness, database/Redis authentication and certificate renewal. Then test
provider-specific streaming, tools and Responses compatibility when onboarding
models. A healthy gateway with an empty model list proves no provider integration.

Sources: [stable release](https://github.com/BerriAI/litellm/releases/tag/v1.109.1),
[versioned upstream chart](https://github.com/BerriAI/litellm/tree/v1.109.1/helm/litellm-helm),
[CNPG client certificates](https://cloudnative-pg.io/documentation/current/certificates/),
[LiteLLM coordination Redis](https://docs.litellm.ai/docs/proxy/caching),
[Enterprise feature boundary](https://docs.litellm.ai/docs/enterprise).
