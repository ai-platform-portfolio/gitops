# Azure Service Operator

Each cluster root installs the official chart **2.21.1**, running **v2.21.1**,
verified as the latest stable release on 2026-10-23. The explicit Application in
`../../templates/azure-service-operator.yaml` runs at wave 10, after cert-manager
at wave 5. Every root includes the operator, its namespace and network policy.
One replica with upstream 200m CPU/256Mi memory requests and 500m/512Mi limits
fits the initial non-HA portfolio; its disruption budget is disabled so a single
replica does not block node maintenance. Upgrades use the upstream Recreate strategy
and briefly interrupt reconciliation and admission for ASO resources.

## Ownership and CRDs

Terraform retains ownership of existing AKS, networking, platform identities/roles,
storage containers and the initial Argo installation. Enabling Atlantis lets its
package create its own resource group, identity, federation and roles through ASO; no existing
Terraform resources are adopted. This package installs an operator; it contains no Azure
resource instances and creates no billable Azure services.

ASO installs and upgrades its own CRDs using `installCRDs: true`. Enabled groups
make the following APIs available to future GitOps consumers:

| CRD pattern | Resources |
| --- | --- |
| `resources.azure.com/*` | Resource groups |
| `managedidentity.azure.com/*` | User-assigned managed identities and federated identity credentials |
| `authorization.azure.com/*` | Role assignments and custom role definitions |
| `storage.azure.com/*` | Storage accounts, Blob services/containers and other storage child resources |

The remaining service APIs are already installed by their package operators:

| Resource | Existing GitOps API |
| --- | --- |
| In-cluster PostgreSQL databases | CNPG `Database` (`postgresql.cnpg.io/v1`) |
| PostgreSQL application roles | CNPG `Cluster.spec.managed.roles` |
| Application credential delivery | External Secrets `ExternalSecret` and `ClusterSecretStore` |
| Generated application passwords | External Secrets `Password` (`generators.external-secrets.io`) |
| PostgreSQL backups and schedules | CNPG `Backup` and `ScheduledBackup` |
| Backup object-store settings and retention | Barman `ObjectStore` (`barmancloud.cnpg.io/v1`) |

Validation checks these CRDs and CNPG role schema alongside the enabled ASO groups.
Kubernetes Secrets themselves use the built-in API and need no additional CRD.

Enabling these APIs creates no Azure resource instances or credentials. Existing
PostgreSQL databases and roles use CloudNativePG CRDs; no Azure PostgreSQL group
is needed for the in-cluster database. Avoid `*`: installing every supported API
adds unnecessary API-server load. Consumers supply scoped permissions and
lifecycle tests when they start using an API.
Removing a pattern does not delete existing CRDs; ASO still upgrades installed
CRDs. These runtime-created CRDs are outside Argo's tracked resource inventory.

Future consumer Applications must run after wave 10, own their Azure CRs and
have health checks for the current generation's ASO Ready condition before
dependent workloads start. Do not put ASO resource instances in the shared wave-10
configuration Application: it runs concurrently with the operator. Do not adopt
Terraform-managed Azure resources without an explicit ownership migration.

## Authentication and permissions

The chart creates the required `aso-controller-settings` Secret with empty global
client, tenant and subscription fields. No platform identity is reused. It enables
workload identity and projects a service-account token with audience
`api://AzureADTokenExchange` itself; an Azure workload identity webhook annotation
is not needed for this path. No client secret or Azure CLI is required by ASO.
The repository's existing bootstrap/lifecycle tools still require Azure CLI.

Bootstrap now supplies a dedicated `identities.aso` managed identity through the
existing reusable module loop. It trusts its cluster controller service account
with audience `api://AzureADTokenExchange`, has Managed Identity Contributor on
the cluster resource group and Storage Account Contributor plus Role Based Access
Control Administrator on the existing application storage account. GitOps builds
Testkube's namespace `aso-credential` from runtime client/account context. No
manual provisioner Key Vault secret is required for that package. The global
credential remains empty; Atlantis retains its separately configured authority.

On management, GitOps also creates `cluster-onboarding/aso-credential` from
the existing ASO bootstrap identity and account context. Foundation Azure CRs
use this namespace credential while provisioning a new cluster; the new
cluster's own ASO identity is used only after its local platform installs.

The base installation watches `azure-resources` and `testkube`, plus
`cluster-onboarding` on management; enabled Holmes
and optional Atlantis add their namespaces. New consumers must supply scoped namespace credentials and run
after ASO health. Never let ASO reconcile Terraform-owned resource instances.
Namespace writers can exercise the credential's Azure permissions, even without
Secret reads. The upstream controller also has broad Kubernetes/CRD permissions;
the watch filter is not tenant RBAC isolation. Restrict authors accordingly.

The shared `configuration` Application creates an `aso-credential` only in the
platform-owned `azure-resources` namespace. This lets trusted GitOps Applications
place new application Azure CRs in the namespace ASO already watches, without
opening every dynamic PR namespace to ASO. The operator credential is not
reflected into application namespaces. An ASO-generated identity ConfigMap can
instead carry Reflector annotations to copy its public client ID to one approved
application namespace. A shared preview workload identity still needs a
federated credential for each preview namespace and ServiceAccount; its lifecycle
belongs to the later preview delivery work. The central credential does not
authorize adopting Terraform-owned Azure resources.

The package policy permits DNS to kube-system, outbound HTTPS/API traffic and
inbound webhook/probe/secure-metrics ports. It avoids upstream SQL egress rules.
HTTPS destinations are unrestricted; this policy does not implement a company
data perimeter. Internal cert-manager Certificates serve ASO webhooks/metrics;
they are separate from public ListenerSet certificates and need no public route.

## Validation and operations

`make validate` checks both cluster roles, dependency wave, pinned
image, enabled CRD groups, projected token, empty global credential, single
replica and stable Secret rendering, alongside every existing pinned chart.
Rendering does not verify Kubernetes admission, runtime CRD installation or Azure
authentication. No deployment or Azure CRUD validation was performed for this PR.

After deployment, check the `azure-service-operator` Application is Synced/Healthy,
the controller rollout succeeds, both internal Certificates are Ready and
the CRDs for the enabled groups are Established. In an approved disposable
Azure scope, verify a consumer reaches Ready for its current generation, handles
an Azure permission denial, detects drift and completes deletion. Check a resource
outside the watched namespace is not reconciled. Measure controller/API usage
before expanding the CRD set or reducing the Azure resync interval.

Before upgrades, review release notes and removed API/storage versions, run the
renders, and test against a disposable cluster. Upgrade the operator and CRDs
together; a Helm rollback alone may not undo CRD/storage-version migrations.

Remove consumer Applications/CRs and wait for ASO finalizers and intended Azure
deletion before `make down`. Keep the operator and credential available throughout
deletion. The lifecycle fails before stopping the platform when unrelated namespaced Azure
CRs remain; only exact resources tracked by the platform-managed `atlantis-azure` or `testkube-azure`
Applications may be removed through its ordered package cleanup. It never force-removes finalizers. Use an explicitly reviewed reconcile
policy if Azure resources must survive. Never place Terraform state resources
under disposable ASO ownership. The operator does not remove CRDs on uninstall;
they remain on a retained cluster and disappear with a destroyed AKS cluster.

Sources: [release](https://github.com/Azure/azure-service-operator/releases/tag/v2.21.1),
[Helm installation](https://azure.github.io/azure-service-operator/guide/installing-from-helm/),
[CRD management](https://azure.github.io/azure-service-operator/guide/crd-management/),
[credential scope](https://azure.github.io/azure-service-operator/guide/authentication/credential-scope/),
[credential format](https://azure.github.io/azure-service-operator/guide/authentication/credential-format/),
[security boundary](https://azure.github.io/azure-service-operator/guide/security/confused-deputy/).

The Application ignores only the exact two namespace exclusions AKS adds to its
webhooks (`control-plane=true` and `kubernetes.azure.com/managedby=aks`). The pinned
chart has no selector setting. Namespace labels remain compared. Additional or changed selector expressions still
produce drift; webhook rules and failure policy remain compared normally.
