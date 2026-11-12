# Entra identities with Crossplane

Argo installs Crossplane 2.4.3, AzureAD provider 2.5.0 and the standard
patch-and-transform function 0.11.1 on each cluster.
The three Argo stages share `templates/crossplane.yaml`; separate Applications
preserve dependency health gates without separate template files. Langfuse
dependencies share its existing template. There is no Azure ARM provider or SQL
provider in this change. ASO keeps ownership
of Azure service dependencies; CloudNativePG keeps ownership of PostgreSQL.

## Argo and Crossplane ownership

| Stage | Parent wave | Responsibility |
| --- | --- | --- |
| `crossplane` | 0 | Install the core controllers and package APIs |
| `crossplane-packages` | 5 | Install the AzureAD provider, function, federated runtime and activation policy |
| `crossplane-identity-api` | 6 | Install the provider configuration, OIDC API and composition |
| `service-identities` | 11 | Declare identities and assignments after the shared secret store exists |
| `langfuse` | 40 | Consume the generated credential through native OIDC |

Argo owns the manifests, including each `OIDCApplication` request. Crossplane
composes its application registration, enterprise principal, password and final
Kubernetes Secret. The AzureAD provider owns the corresponding Graph resources.
Argo does not also render those composed resources or their Secret. Assignment
resources are directly owned by Argo and reconciled by the provider.

The Argo ConfigMap contains health checks for packages, composite API establishment,
identity reconciliation and assignments. These gates hold later waves until the
controllers and identities are ready. Argo still manages workload rollout; it does
not execute Graph API calls. Teardown reverses the child Application waves so Graph
resources are removed while the provider and its authentication still exist.

Crossplane's first-install init step creates the `default` managed resource
activation policy from Helm values. It does not update that policy when the
activation list changes. Argo therefore reconciles the same policy in
`crossplane-packages`, using the activation list from the core chart values as
the single source. This activates new provider CRDs on an existing cluster.

## Controller authentication

Terraform bootstraps only the controller's identity using the existing reusable
Entra module. This breaks the initial authentication/consent dependency: an
unprivileged controller cannot grant itself Graph permissions. No service callback,
allowlist or service credential is configured in Terraform.

`entra_applications.crossplane` in bootstrap tfvars explicitly requests and consents
`Application.ReadWrite.OwnedBy`, `Application.Read.All` and
`AppRoleAssignment.ReadWrite.All`. The last permission is tenant-wide and powerful;
application ownership restricts registration writes but does not scope that grant.
The actor running Terraform needs directory authority to consent these permissions;
Azure subscription Contributor alone is insufficient. A failed consent must stop
`make up`, not fall back to a password or another identity.

The controller's client/principal IDs enter the existing sensitive runtime object;
tenant context comes from the authenticated account, not a checked-in value or a
new Terraform output. A projected service-account token, with audience
`api://AzureADTokenExchange`, authenticates the fixed `provider-azuread` service
account. Its explicit mount matches the provider configuration and does not depend
on an AKS workload-identity webhook's default path. No controller client secret is
created, and there is no directory-user write permission.

## OIDC API and credentials

`identity-api.yaml` groups the provider configuration, API definition and
composition. Together they define a small namespaced
`OIDCApplication` API. It creates a single-tenant application, a principal requiring
assignments, a generated password and an aggregated connection Secret in the same
namespace. The standard function combines the public application client ID and
tenant ID with the provider's `attribute.value` connection detail. Passwords never
pass through Helm values, Argo manifests, Terraform or an imperative Graph script.
They do exist in Kubernetes Secrets and provider processing/state; this is not a
claim that Crossplane removes the need to secure cluster Secret storage, RBAC and
backups. Debug logging must remain disabled for credentials.

No existing registration is adopted. Duplicate application names fail rather than
silently taking ownership. Delete semantics are deliberate: deleting the request
removes its registration, enterprise principal and password. They are disposable
with this cluster; Argo's bootstrap SSO and retained Sonar SAML registration are
separate and are not adopted or deleted by this package.

The password lifetime defaults to the explicit Langfuse GitOps choice of 180 days.
A namespace-scoped Reloader watches Langfuse Secrets and restarts the annotated web
Deployment when delivered credentials change. It has no global Secret watch. Argo ignores only the reload marker on the
Langfuse web pod template, preventing self-heal from undoing the restart; other
Deployment drift remains visible.
**Automatic time-based credential rotation is not configured.** Before expiry,
replace the composed Password through a controlled operational rotation and verify
new Secret delivery and web rollout. Changing an immutable password parameter is
not assumed to provide a safe overlapping rotation. A future rotating credential
policy must explicitly handle overlap and retirement; it must not use Helm `now`
or silently recreate passwords on every sync.

## Validation and acceptance

The full chart validator renders pinned core, provider configuration and workload
charts. The composition was also executed locally with Crossplane's renderer and
synthetic observed resources/connection Secret; the aggregate Secret contained
`client-id`, `client-secret` and `tenant-id`. Composed resources were checked against
released AzureAD CRDs. Those checks do not create Graph resources or prove browser
login. After merge, `make up` must establish the controller identity, healthy
packages, ready identity/assignments, credential Secret and Microsoft redirect.
An assigned-user login and rejected unassigned-user login remain live acceptance
checks. Keep Sonar's recovery administrator available until those pass.

Sources: [Crossplane providers](https://docs.crossplane.io/latest/packages/providers/),
[patch-and-transform connection details](https://docs.crossplane.io/latest/guides/function-patch-and-transform/),
[released AzureAD APIs](https://github.com/crossplane-contrib/provider-upjet-azuread/tree/v2.5.0/apis/namespaced).
