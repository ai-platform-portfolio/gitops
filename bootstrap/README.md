# Management cluster bootstrap

One Terraform root builds networking, AKS, its Entra administrator group,
map-driven managed identities,
disposable data containers and the initial ArgoCD Helm release. Subsequent
clusters have independent roots. Inputs belong in tfvars; declarations stay in
`variables.tf`, resource lookups and scope aliases in `main.tf`, authentication
in `provider.tf`.

The management root also declares a new private Foundry account, one management
project, a regional Standard chat deployment, three private DNS zones linked to
the AKS VNet, and a private endpoint in the dedicated subnet. The account uses
its own user-assigned identity. Public network access and local key authentication
are disabled by the pinned module defaults. The existing public `foundry-portfolio`
account is outside this root and is not adopted or changed. Model deployment
capacity, version and region are explicit in `terraform.tfvars`; review quota,
availability and processing requirements again before apply. Azure model usage
is billed even though HolmesGPT itself is free.

HolmesGPT's ASO-owned identity and inference role assignment remain in the
Holmes GitOps package. The Foundry module's optional inference principal is
unset here, avoiding a second owner for that grant. Merge the library change
at Terraform PR #12 before this root's pinned revision is consumed. The Holmes
package still needs its own reviewed model endpoint/API version, private endpoint
CIDR, Kubernetes API and Entra egress ranges, scoped ASO credential, live DNS
resolution and successful workload-identity inference test before enablement.

`argocd-values.yaml` holds deployment settings and group RBAC.
`argocd-configmap.yaml` is the actual commented Argo resource, including OIDC,
installed by the same Helm release. Terraform renders both templates, supplying
only the admin group, canonical URL and Entra context. The `entra-application`
library module creates the SSO application, service principal and federation.
The Argo module adds the existing managed identity's client ID to the configured
service accounts; SSO does not replace that identity. See [sign-in and validation](../README.md#argo-cd-sign-in).

Azure CLI supplies authenticated account context. Tenant/subscription identifiers
are used internally without Terraform outputs. Existing DNS, Key Vault and the
platform storage account are looked up; this root owns their new data containers.
Those containers and their contents are deleted on teardown. There is no
cross-teardown restore or retained-credential store.

Run `make plan` from the repository root to preview Terraform changes, then
`make up` to initialize/apply infrastructure and deploy the full platform
chart. This includes Envoy Gateway, cert-manager, ExternalDNS, databases,
observability, federation and enabled applications. Argo is exposed through its
own issuer-annotated ListenerSet and HTTPRoutes.

The URL is `https://<argocd.dns_label>.<dns.zone.name>`, built directly from the
required tfvars label and zone. Terraform supplies the same URL to
Argo's ConfigMap and the GitOps route. The lifecycle checks DNS ownership before
seeding and waits for a current certificate, current Gateway DNS and verified
HTTPS. Existing unowned exact A/AAAA/CNAME records cause an error instead of being
adopted. No port forwarding is required. Read the initial admin password locally
from `argocd-initial-admin-secret`; never commit it or print it in shared logs.
For Argo CLI access through the HTTPRoute, use `--grpc-web`.

Envoy terminates TLS and forwards HTTP to Argo's ClusterIP service. Argo's namespace
receives only the shared gateway label from GitOps, with `Prune=false,Delete=false`
so package cleanup cannot delete its own controller. Terraform retains ownership
of the Argo Helm release. No duplicate certificate manifest or wildcard certificate
is created; cert-manager generates the per-host certificate from the ListenerSet.

State uses the external Azure Blob backend configured by ignored
`backend.local.hcl`; keep backend details, credentials and state out of Git. Use
`make down` for ordered package cleanup before destroying infrastructure. See the
[configuration and lifecycle guide](../helm_chart/README.md) for prerequisites,
backend setup and validation.

## AKS administrator group

`aks.admin_group` configures the Terraform library's `entra-group` module.
Terraform creates this group before AKS and passes its `object_id` output directly
to the cluster. Set only its display name and description in `terraform.tfvars`.
The authenticated principal from `data.azurerm_client_config.current.object_id`
is both owner and bootstrap member. Membership authorizes Terraform's Kubernetes
provider to install Argo; group ownership alone does not grant AKS access.
No owner/member ID lists are configured in tfvars, and no Azure RBAC roles are
assigned to this group. Applying through a different principal changes this
bootstrap owner/member, so review access when changing deployment identities.

The AzureAD provider uses the calling root's authenticated context; the caller
needs Microsoft Graph authority to manage the group and membership. This is a
bootstrap dependency, managed by the same Terraform root and persistent backend.
Teardown deletes the group along with the disposable cluster. Existing same-name
groups are rejected; adopt an existing group only through deliberate import and
membership review. No one-off CLI group creation is required.

## Deployment inputs

`terraform.tfvars` is the default deployment configuration. Environment metadata
such as principal IDs, resource names and email can be checked in.
An existing `local.tfvars` file is not selected automatically; `TFVARS=...` is an
explicit override. Backend settings stay in ignored `backend.local.hcl`, and
credentials remain in the authenticated context, Key Vault or Kubernetes Secrets.

The PostgreSQL backup destination uses a fresh `management-postgres-backups`
container. The pre-existing `postgres-backups` container is not adopted or deleted
by this deployment. Terraform state uses a separate persistent storage account.

## Public API allowlist

`aks.authorized_ip_ranges_secret` names an existing secret in `platform.key_vault`.
Its value is a JSON array of IPv4 CIDRs. The bootstrap reads it through the
AzureRM secret data source and passes the decoded sensitive value to AKS.
The deployment identity needs permission to read that Key Vault secret.

The value is redacted from normal Terraform output, but persists in state and
saved plans. Keep state in the encrypted Azure backend, restrict its access, and
do not publish raw plan JSON or enable Terraform/provider debug logs for real
plans/applies. The CI validation workflow uses no Azure credentials and does not
read this secret. Updating it changes the allowlist at the next plan/apply; the
existing Key Vault and secret are not deleted with the cluster.

Both AKS node subnets explicitly allow Internet TCP 80/443 through their subnet
NSGs for the public gateway load balancer. Azure evaluates subnet and NIC NSGs;
AKS's managed NIC rules cannot override a subnet deny. The load balancer uses
backend ports 80/443, so the NodePort range is not opened. The private-endpoint
subnet has no public ingress rule. Kubernetes API restrictions remain separate.

Argo uses server-side diff: it asks the Kubernetes API to dry-run the desired
resource before comparing it with the live object. This accounts for CRD defaults,
including Gateway API route matches and backend/certificate references, without
copying those defaults into every manifest. This setting controls comparison;
normal synchronization still uses server-side apply. The chart rolls the
application controller when command parameters change.
