# Atlas operator (free/basic)

`templates/atlas.yaml` installs Atlas Operator/chart 0.8.4 at wave 10 on each
cluster. The namespace is created by the existing wave -10 namespaces Application.
The operator is idle until a later change adds AtlasSchema or AtlasMigration
resources. No existing databases, application schemas or CNPG roles are adopted.

No Atlas Cloud account, token, subscription or interactive portal is configured.
The operator needs no SSO: Kubernetes RBAC controls its API and existing Argo CD
SSO controls deployment management. Atlas Cloud is a separate hosted product;
Entra SSO there is a paid Pro add-on or Enterprise feature, not a local gateway.

Development-database prewarming and custom project configuration are disabled.
The controller watches Atlas resources only in atlas-system. This watch setting
is not an RBAC boundary: upstream ClusterRole permissions for pods, deployments,
ConfigMaps and Atlas resources remain cluster-wide. Cluster-wide Secret access
and Secret watches are disabled; no database credentials are supplied.

Before adding migrations, explicitly choose the target namespace, provide scoped
Secret RBAC (local migration directories also store their state in Secrets),
configure verified database TLS and network policies, and gate dependent apps on
migration readiness. Do not let Atlas and CNPG or application-owned migration
systems manage the same objects. ConfigMap migration directories can be used
without the paid registry. No migration functionality is claimed tested by this
operator-only installation.

Advanced objects, security-as-code, safety policies and paid automation require
an appropriate Atlas licence. A developer seat alone does not license Pro CI/CD.

Sources: [installation](https://atlasgo.io/integrations/kubernetes/install),
[versioned migrations](https://atlasgo.io/integrations/kubernetes/versioned),
[pricing](https://atlasgo.io/cloud/pricing).
