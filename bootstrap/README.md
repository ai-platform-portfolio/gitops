# Management cluster bootstrap

One Terraform root builds networking, AKS, its Entra administrator group,
map-driven managed identities,
disposable data containers and the initial ArgoCD Helm release. Subsequent
clusters have independent roots. Inputs belong in tfvars; declarations stay in
`variables.tf`, resource lookups and scope aliases in `main.tf`, authentication
in `provider.tf`.

The management root also declares a new private Foundry account, one management
project, a regional Standard chat deployment, three private DNS zones and a
private endpoint in the dedicated subnet. The independent `private_dns` map
owns zone definitions; `vnet_link` defaults to `false`, and the reusable network
module creates links only for zones that opt in. Foundry selects zones by key, so
other resources can use the same DNS configuration. The account uses
its own user-assigned identity. Public network access and local key authentication
are disabled by the pinned module defaults. The existing public `foundry-portfolio`
account is outside this root and is not adopted or changed. Model deployment
capacity, version and region are explicit in `terraform.tfvars`; review quota,
availability and processing requirements again before apply. Azure model usage
is billed.

Inference consumers own their identities and account role assignments separately.
The Foundry module's optional inference principal is unset here, avoiding a
second owner for those grants. Both modules are pinned to the merged Terraform
PR #13 revision.

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

## Staging and production PostgreSQL foundation

The bootstrap calls the pinned `postgresql-flexible-server` module once. It
creates one private Azure Flexible Server for both persistent environments,
using the existing private endpoint subnet and a VNet-linked
`privatelink.postgres.database.azure.com` zone. Its PostgreSQL version, compute,
storage, backup retention and name are grouped in `postgres` in
`terraform.tfvars`. The server accepts Microsoft Entra authentication only.
The authenticated account context supplies the tenant ID; no tenant ID or
database password is configured in tfvars.

The existing AKS administrator group is also the server's Microsoft Entra
administrator. The caller passes the group's object ID directly from
`module.aks_admin_group`; its display name comes from the same `aks.admin_group`
configuration. Group membership therefore grants database administration as
well as AKS administration. Review membership changes with both privileges in
mind. Application pods will use separate ASO-managed identities and federated
credentials, with SQL permissions scoped to their own databases.

This foundation creates no staging/production SQL databases, roles or application
connections yet. They belong to the reusable application delivery templates.
`make down` cleans Kubernetes consumers first, then Terraform destroys this
disposable server and its data with the other bootstrap resources. The remote
Terraform backend remains persistent.

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

The map-driven `identities.aso` entry supplies the ASO controller's initial
federated authority on the management resource group and existing application
storage account. `platform_settings.runtime` carries its client ID and the cluster
resource-group name; GitOps supplies namespace credentials and owns service
identities/resources. This resolves the circular dependency of ASO provisioning
its own first credential. See [Testkube sequencing](../helm_chart/platform/packages/testkube/README.md).

## Model gateway foundation

`model_gateway` in `terraform.tfvars` deploys private Standard v2 API Management
in UK West, with its own delegated integration subnet and managed identity. The
same reusable network module creates both VNets; a `moved` block preserves the
existing management network addresses in state. Existing AKS and Foundry stay in
UK South. The gateway creates no APIs or subscriptions for application callers.
Publisher contact values are demo metadata; replace the unmonitored example
address before production use.

**Why UK West:** Microsoft's availability table currently marks new APIM v2
instances in UK South temporarily unavailable. Standard v2's integration VNet
must be in the same region as APIM, so the gateway uses a separate UK West VNet.
This does not relocate the existing cluster or Foundry account.
[Regional availability](https://learn.microsoft.com/en-us/azure/api-management/api-management-region-availability)
and [integration requirements](https://learn.microsoft.com/en-us/azure/api-management/integrate-vnet-outbound).

The private endpoint uses `privatelink.azure-api.net`. Azure requires creating
that endpoint before public access can be disabled, so the module orders a small
AzAPI property update after endpoint creation. AzureRM owns the service and
ignores only that property; AzAPI owns its final Disabled value. No inference
routes exist during the initial public provisioning window. APIM provisioning
and teardown can each take hours; its fixed running charge and Private Link/DNS
charges continue until teardown completes. Microsoft retail pricing checked on
2026-11-05 lists the UK West Standard v2 first unit at USD 0.9589/hour
(about USD 23/day or USD 700 per 730-hour month), excluding other charges, tax
and agreement discounts. Recheck pricing before each deployment.
[Retail pricing API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).

A failed apply can leave APIM public
until the ordered final update succeeds; do not publish APIs before that gate.

This is the original foundation port, **not working model routing**. No VNet
peering, Google private transport, provider APIs, token exchange, model aliases,
caller authorisation, token quotas or inference runtime validation are delivered
here. The isolated gateway is not yet reachable from the management VNet; adding
that connectivity belongs with the reviewed routing stage.

The reusable `modules/gcp/workload-identity` module is available in the Terraform
library. The intended project is the existing GCP project with display name
**Main**. This port does not call it from bootstrap: the original gateway root
also had no GCP caller, and its dedicated Entra audience/role and APIM token
exchange are not implemented yet. Resolve Main to its actual project ID when
that stage is implemented; do not choose a project solely by a display-name match.
No Google provider or service-account key is required by this bootstrap stage.

Follow-up discussion: wire the existing Foundry backend and Google Gemini route,
configure keyless federation through the existing Entra ownership layer, select
approved models/regions, define caller permissions and consumption controls, then
prove streaming, token renewal, denied access and ordered cleanup. Foundation
validation alone must not be reported as successful inference.

Run this root with Terraform 1.9+ (CI pins 1.9.8), using the existing Azure backend.
Do not apply a whole-root plan without reviewing unrelated drift, including any
explicit operator access exceptions on Foundry. The library commit is pinned in
each module call. Review the plan for zero management-network replacements.
