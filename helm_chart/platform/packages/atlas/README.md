# Atlas operator and Cloud token

`templates/atlas.yaml` installs Atlas Operator/chart 0.8.4 at wave 20 on each
cluster. The namespace is created at wave -10. At wave 10, External Secrets
copies the `ai-platform-portfolio-atlas-bot` secret from the existing platform Azure Key
Vault to `atlas-system/atlas-token-secret` with key `ATLAS_TOKEN`. Argo waits for
that ExternalSecret to become Ready before installing the operator. The Helm
chart reads the token through `extraEnvs`; the token never enters Git or Argo
values.

Before merging this change, create an Atlas Cloud bot in the
`ai-platform-portfolio` organization (Settings > Bots), then store its token as
the `ai-platform-portfolio-atlas-bot` secret in the platform Key Vault. The token is shown
only once. The existing `azure-kv` ClusterSecretStore supplies it on each
cluster. If the Key Vault secret is missing, the configuration Application stays
unhealthy and the operator does not roll out with an empty token.

The chart leaves persistence disabled while no Atlas resources exist. When
Atlas starts managing database objects, assess a 1 GiB PVC for the cached Pro
grant so a pod restart during a Cloud outage does not lose offline access.
The operator is idle until a later change adds AtlasSchema or AtlasMigration
resources. No existing databases, application schemas or CNPG roles are adopted.

The operator needs no separate application SSO: Kubernetes RBAC controls its API
and existing Argo CD SSO controls deployment management. Atlas Cloud account
sign-in is separate from this operator credential.

Development-database prewarming and custom project configuration are disabled.
Non-root and seccomp settings live on the container because the upstream chart
defaults fsGroup and skips podSecurityContext; null overrides are lost through
Argo valuesObject.
The controller watches Atlas resources only in atlas-system. This watch setting
is not an RBAC boundary: upstream ClusterRole permissions for pods, deployments,
ConfigMaps and Atlas resources remain cluster-wide. Cluster-wide Secret access
and Secret watches are disabled; no database credentials are supplied.

Before adding migrations, explicitly choose the target namespace, provide scoped
Secret RBAC (including any `cloud.tokenFrom` reference; remote migration
directories require this reference even with operator-wide `ATLAS_TOKEN`),
and account for local migration directories storing state in Secrets,
configure verified database TLS and network policies, and gate dependent apps on
migration readiness. Do not let Atlas and CNPG or application-owned migration
systems manage the same objects. ConfigMap migration directories can be used
without the registry. No migration functionality is claimed tested by this
operator-only installation. The Cloud token does not itself enroll a database
or create a Registry project.

Advanced objects, security as code, safety policies and paid automation require
the appropriate Atlas licence. The Pro trial must cover the intended operator
usage; a local developer sign-in is not an operator credential.

Sources: [installation](https://atlasgo.io/integrations/kubernetes/install),
[versioned migrations](https://atlasgo.io/integrations/kubernetes/versioned),
[pricing](https://atlasgo.io/cloud/pricing).
