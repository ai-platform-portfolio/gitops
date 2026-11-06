# Management cluster bootstrap

One Terraform root builds networking and AKS using an existing Entra administrator group,
map-driven managed identities,
disposable data containers and the initial ArgoCD Helm release. Subsequent
clusters have independent roots. Inputs belong in tfvars; declarations stay in
`variables.tf`, resource lookups and scope aliases in `main.tf`, authentication
in `provider.tf`.

## Kubernetes NetworkPolicy status

The management Argo instance excludes Kubernetes `NetworkPolicy` resources from
reconciliation through `argocd-configmap.yaml`. Package policy manifests remain
staged; their ingress/egress rules are not enforced in this deployment. Namespace
placement, CIDR values and successful template tests do not establish network
isolation. AKS-managed policies in `kube-system` are separate from these packages.
Re-enabling application policies is deferred and requires an explicit decision.

Kubernetes RBAC, application authentication, TLS, Azure NSGs and private endpoints
remain separate controls. Their presence does not imply pod-network isolation.

## Platform dependencies

The management root also declares a new private Foundry account, one management
project, a regional Standard chat deployment, three private DNS zones and a
private endpoint in the dedicated subnet. The independent `private_dns` map
owns zone definitions; `vnet_link` defaults to `false`, and the reusable network
module creates links only for zones that opt in. Foundry selects zones by key, so
other resources can use the same DNS configuration. The account uses
its own user-assigned identity. Public network access and local key authentication
are disabled by the pinned module defaults. The approved live operator exception
enables selected-IP public access with default-deny and 11 IP rules, while local
authentication remains disabled and the private endpoint remains available. This
exception is not yet represented in Terraform: a full-root apply would remove it
and must be reviewed. Actual IP ranges stay out of Git.

The existing public `foundry-portfolio`
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

On a resumed startup, Argo may detect a newer Git revision after the root is
seeded. The existing PostgreSQL readiness wait also checks for a superseded root
sync, so the lifecycle can end the old operation and let Argo reconcile the new
revision before PostgreSQL is available. Current-revision syncs are left running;
database readiness, credential preservation and TLS checks still apply.

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

## AKS control-plane tier

`aks.sku_tier` selects the existing AKS module's control-plane tier. The caller
sets `Standard`; omitting the input preserves the module's `Free` default for
other deployments. This choice does not change worker sizes or node counts.

Azure ResourceHealth reported that this controller-heavy platform overloaded its
Free control plane and triggered automatic restarts. The native AKS diagnostic
also reported low inflight-request limits and recommended Standard for additional
control-plane replicas/resources. Worker scaling does not directly enlarge the
managed API server. Standard adds capacity and availability headroom; the Argo
WATCH backoff and bounded LIST settings below remain necessary, and successful
reconciliation must be verified after activation.

UK South Standard management retail pricing checked 2026-11-05: GBP0.0752 per
cluster-hour, approximately GBP54.90 for 730 hours, excluding tax and any
subscription discounts. Worker, storage and network charges are separate.
See [AKS tiers](https://learn.microsoft.com/en-us/azure/aks/free-standard-pricing-tiers)
and [retail pricing](https://azure.microsoft.com/en-us/pricing/details/kubernetes-service/).

## AKS administrator group

`aks.admin_group.display_name` selects an existing, security-enabled Entra
operator group. Its identity and membership persist across disposable rebuilds;
operators manage membership in Entra. The group also grants PostgreSQL
administration and Argo access, so review changes with all three in mind.
Ownership alone does not grant access: the bootstrap caller must be a member,
with a current token reflecting that membership. A new installation needs this
operator group established once, alongside existing DNS, Key Vault and backend
prerequisites. No client secret or per-application group setup is introduced.

The current group's object is preserved through a declarative `removed` block
with `destroy = false`, then read through the existing display-name input. Apply
this reviewed migration with native `make up` **before the next `make down`**.
Terraform 1.9.8 destroy mode ignores this preservation instruction for an object
still in state; the normal migration apply must first detach its ownership.
Review the plan for one group forget action, no group deletion/replacement,
and unchanged AKS/PostgreSQL administrator IDs. No manual state command is needed.
After migration, the group remains an external prerequisite and survives teardown.

The AKS gate requires three consecutive API-health and cluster-wide bootstrap
permission checks before Helm can run. API reachability alone is insufficient.
A readiness-script hash and group identity participate in its Terraform trigger,
so updates rerun the gate. The gate has a ten-minute limit and reports permission
failures without printing tokens. Azure CLI caches group claims; after membership
changes, a fresh sign-in may be needed once. The stable group prevents each
rebuild from invalidating the identity referenced by otherwise valid tokens.
This does not manipulate CLI caches or change the authentication mode.

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
`data.azuread_group.aks_admin`; its display name comes from the same `aks.admin_group`
configuration. Group membership therefore grants database administration as
well as AKS administration. Review membership changes with both privileges in
mind. Application pods will use separate ASO-managed identities and federated
credentials, with SQL permissions scoped to their own databases.

This foundation creates no staging/production SQL databases, roles or application
connections yet. They belong to the reusable application delivery templates.
`make down` stops root reconciliation, then removes its directly tracked Sensors
before their EventBus and Argo Events controller. Each Sensor requires an exact
root tracking annotation; normal finalizers are awaited, including on retry.
Sensors outside the root inventory are untouched. The lifecycle then drains its
tracked onboarding ApplicationSets with foreground cascading deletion. It records descendants using
native owner references and Argo tracking, then waits for their Application UIDs
to disappear as well: nested ApplicationSets can be gone while application
PostDelete hooks still run. Their registration Applications remove consumer
delivery definitions and run existing application PostDelete cleanup while Atlas,
ASO, credentials, PostgreSQL and routing remain available. No per-repository cleanup list or application values are required.
The lifecycle then checks for remaining Atlas/database/Rollout/ASO consumers.
Atlas hooks already marked for deletion get a bounded finalizer wait; active
Atlas leftovers still fail immediately. The ASO guard derives platform ownership
from the root Application inventory, managed package label and exact Argo tracking
on both the package and Azure resource. Shared packages need no name allowlist;
unrelated Azure resources remain blockers. API read errors or stuck finalizers
stop teardown before platform dependencies are deleted. Retrying resumes with the
remaining resources.

ExternalDNS starts at wave 15, after configuration and before every public route
package. Reverse teardown keeps it running until routes are removed, then waits
up to three minutes for its owned DNS registry to drain before deleting the
controller. This checks actual DNS state instead of sleeping after one route.

Platform packages are then removed in reverse wave order before Terraform
destroys this disposable server and the other bootstrap resources. The remote
Terraform backend remains persistent.

After package cleanup, native teardown checks the AKS node resource group's
`managedBy` link and requires every scale set's `aks-managed-poolName` tag to
match an AKS-reported pool. It cancels active rolling upgrades through the native
Azure Compute command before Terraform destroys the node pools. Azure platform
VM Agent upgrades can otherwise reject deletion with `OperationNotAllowed`.
Cancellation uses Azure CLI's operation wait with a three-minute local timeout;
completed, cancelled, faulted or absent upgrades need no cancellation. Ownership,
permission, status-read and cancellation errors stop teardown. This Compute action
is limited to disposable teardown, not normal AKS maintenance or node changes.
No upgrade policy is disabled. A new platform upgrade starting after the check
can still race with deletion; this is not an atomic maintenance lock.
See [the native cancellation API](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-scale-set-rolling-upgrades/cancel).

If an interrupted Terraform destroy has already removed bootstrap outputs,
`make down` reads connection metadata internally from Terraform's remaining
state. It refuses ambiguous AKS state or any still-tracked Helm/Kubernetes
resources. For a remaining AKS cluster, the authenticated account must match;
live checks require no workloads outside `kube-system`, no Argo/Atlas/CNPG/Upbound
consumers and no ASO-labelled custom resources before infrastructure-only cleanup
can continue. Empty namespaces and retained Argo AppProjects are harmless.
Read failures stop teardown.
If AKS is already absent from state, no cluster access or upgrade cancellation is
needed. Hub-connected teardown cannot use this fallback without outputs.
This recovery performs no Terraform apply or state edit, never logs raw state,
and is unavailable to startup. Terraform state contains sensitive values; its
JSON is captured internally, with only connection metadata retained.


A repeated `make down` after completed destruction reads owner/zone/group inputs
through native Terraform console with the same `TFVARS`. It requires empty state
and an absent disposable resource group before cleaning stale DNS. Only the
platform's IPv4 A/TXT registry pairs with exact owner, heritage and HTTPRoute
provenance are supported. The ownership marker is rechecked; both deletions use
ETags. Mixed ownership, unsupported registry formats, missing ETags, read errors
or concurrent changes stop cleanup. Unrelated records and the backend remain
untouched. This path creates no cluster and performs no Terraform apply.

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

When that consumer is implemented, it supplies the custom role ID, title,
description and exact Gemini permissions (including `aiplatform.endpoints.predict`
and any required API-usage permission). Those are deployment choices; the library
contains no model-specific role names or permission defaults.

Follow-up discussion: wire the existing Foundry backend and Google Gemini route,
configure keyless federation through the existing Entra ownership layer, select
approved models/regions, define caller permissions and consumption controls, then
prove streaming, token renewal, denied access and ordered cleanup. Foundation
validation alone must not be reported as successful inference.

Run this root with Terraform 1.9+ (CI pins 1.9.8), using the existing Azure backend.
Do not apply a whole-root plan without reviewing unrelated drift, including any
explicit operator access exceptions on Foundry. The library commit is pinned in
each module call. Review the plan for zero management-network replacements.


## Argo API request recovery

The controller image retains the delete-hook race backport and adds an adaptation
of [upstream watch backoff PR29886](https://github.com/argoproj/argo-cd/pull/29886)
to the pinned3.5.5 source (upstream proposal head
`d774715c74dde47687f02dc561e1db0d8829341d`). The upstream proposal is unmerged; this is a temporary
maintained backport, not a released native capability. Remove each patch only
when the selected upstream release includes its fix and regression tests pass.

### Released-image alternatives checked

Source comparison on 2026-11-05 covered upstream 3.6.1, 3.5.7, 3.4.13,
3.3.19, 3.2.12 and 2.14.21. All retain the flat one-second RetryWatcher path,
and their selected client-go versions bypass QPS limiting for WATCH requests.
Downgrading these images would therefore retain the observed retry storm.

| Versions | Deletion-hook behaviour in inspected source |
| --- | --- |
| 3.3.19–3.6.1 listed above | `AlreadyExists` is skipped without checking the live hook; the existing cleanup patch remains relevant. |
| 3.2.12 and 2.14.21 | Creation conflicts return an error instead of silently completing; the watch-retry issue remains. |

This was source verification, not a live downgrade/compatibility test. No inspected
released image resolves both requirements, which is why this image retains the
existing cleanup fix and adds watch backoff. Reassess when the upstream fixes ship.
See [older engine retry code](https://github.com/argoproj/gitops-engine/blob/c08b0a72c1f1/pkg/cache/cluster.go),
[older client WATCH limiter bypass](https://github.com/kubernetes/client-go/blob/v0.31.0/rest/request.go),
[3.4.13 hook handling](https://github.com/argoproj/argo-cd/blob/v3.4.13/controller/hook.go)
and [3.2.12 hook handling](https://github.com/argoproj/argo-cd/blob/v3.2.12/controller/hook.go).

### Recovery settings and acceptance

`ARGOCD_CLUSTER_CACHE_WATCH_RETRY_USE_BACKOFF=true` backs off qualifying watch
connection failures with a base delay increasing from one to thirty seconds,
plus 10% jitter. Retry state resets after a two-minute stable watch window. Retryable failures preserve
the latest delivered resource version; an expired version still performs the
normal fresh LIST. client-go consumes bookmarks internally, so quiet resources
can still require relisting after expiry. Active-stream errors handled internally
by client-go are outside the connection-failure interception; this is not a
blanket rate limit on every watch operation.

`ARGOCD_CLUSTER_CACHE_LIST_SEMAPHORE=5` bounds initial/recovery LIST concurrency
(upstream50). Five is a conservative starting limit, not a demonstrated optimum.
WATCH reconnects bypass client-go's normal QPS limiter, so lowering QPS alone
does not address this failure. Neither setting disables watch/event delivery,
server-side diff, admission validation or dependency health gates.

Azure ResourceHealth recorded control-plane overload and automatic restart on
2026-11-05. Argo logs then showed a watch retry storm. Regression tests establish
backoff/resource-version behaviour; rendered configuration and CI publication
are separate from live acceptance. Terraform owns this Argo Helm release: merging
a PR does not by itself apply bootstrap values. Review the infrastructure plan
before activation, preserving unrelated live settings. Verify API process uptime,
request/retry rates, controller leases and automatic registration reconciliation
under comparable startup/recovery load before claiming the reliability fix works.
No AKS tier or worker-size change is part of this repair.
