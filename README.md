# GitOps

A federated Argo CD platform on Azure AKS. Each cluster reconciles the shared
platform independently; management onboards workload clusters.

Terraform provisions the management infrastructure and installs its initial
Argo CD Helm release. The platform Helm chart defines shared platform services
that Argo CD reconciles, with readiness gates between dependencies. One Make
command applies the bootstrap and reconciles the full platform,
including Argo CD access through Envoy and a per-host HTTPS certificate.

Workload registrations live separately under [applications](applications/preview-manual).
They consume deployment values from the application repository and pin the reusable
application chart. Registering or syncing them requires separate approval.

## Before you start

Run the commands below from this repository's root directory. Install Terraform
>=1.5, Azure CLI, kubectl, kubelogin and Python 3. Authenticate with Azure CLI and
select the intended subscription.

Review the deployment configuration and configure the state backend:

1. Review `bootstrap/terraform.tfvars`, including the AKS version, admin-group
   name, Key Vault allowlist secret name and existing Azure resource references.
   This tracked file is the deployment configuration.
2. Create `bootstrap/backend.local.hcl` for the existing Azure Blob Terraform
   state backend. Use the example in the
   [configuration guide](helm_chart/README.md#remote-terraform-state).

Terraform creates the Entra security group described by `aks.admin_group` and
passes its object ID directly to AKS. The authenticated Terraform principal owns
the group and is its bootstrap member through
`data.azurerm_client_config.current.object_id`. This membership authorizes the
initial Argo Helm installation; ownership alone does not grant cluster access.
No owner/member IDs are configured in tfvars. The caller needs Microsoft Graph
permissions to manage the group. No Azure role assignments are granted to it. The group and its managed memberships are removed
with this disposable cluster on `make down`.

Make uses `bootstrap/terraform.tfvars`. To select another environment explicitly,
set `TFVARS=/path/to/settings.tfvars`; files named `local.tfvars` have no special
priority. Override the backend or root with `BACKEND_CONFIG=/path/to/backend.hcl`
or `BOOTSTRAP_DIR=/path/to/bootstrap`.

Principal IDs, resource names and the ACME email are environment metadata, so
they can live in the tracked deployment configuration. Credentials,
private keys, Terraform state and backend configuration stay out of Git.

The portfolio configuration uses one Standard_D4as_v4 system node and two
Standard_D4as_v4 application nodes (4 vCPUs / 16 GiB each). A single application
node supports eight attached Azure disks, all consumed by the shared database and
observability stack; the bundled Langfuse stores need five more. Two application
nodes provide 16 disk slots without placing applications on the system node.
This uses 12 of the 18 available Dasv4-family vCPUs in UK South, or 16 with one
surge node. Upgrade pools one at a time within that quota. Both regional and
VM-family quotas must cover the deployment and upgrade surge. Physical capacity
is allocated only at deployment.
See the configuration guide for prerequisites and limits.

The AKS API is public and restricted by the CIDRs in the existing Key Vault secret
named by `aks.authorized_ip_ranges_secret`. The secret contains a JSON array of
CIDRs, currently covering the operator's public IPv4 plus five addresses on each
side. Terraform reads it directly; the values stay out of Git and are redacted
from normal plan/apply output. They are stored in the encrypted remote state and
any saved plan, so restrict access to both and do not publish plan JSON or enable
Terraform/provider debug logging when using the real secret.

## Plan and deploy the platform

Preview the Terraform infrastructure and initial Argo CD installation
(GitOps chart changes are validated separately with `make validate`):

```sh
make plan
```

Deploy the full platform when you are ready:

```sh
make up
```

Both commands initialize Terraform automatically and use the authenticated Azure
CLI account context. There is no separate `terraform init` or environment-variable
setup to run. `make up` automatically approves Terraform apply; it computes
its plan at apply time rather than consuming a saved plan from `make plan`.

`make up` creates networking, AKS, managed identities, disposable application
data containers and the Argo CD Helm release. It then seeds the full platform
chart into Argo CD, prepares application credentials and waits for reconciliation.
Before Helm contacts a newly created or updated AKS API, Terraform waits for
three consecutive authenticated readiness responses. This covers the period
when Azure has completed cluster or API allowlist changes but client access has
not yet propagated; it fails after ten minutes with an allowlist/access hint.
Envoy Gateway, cert-manager, ExternalDNS, databases, observability and the enabled
applications are included. Packages configured as disabled remain disabled.
On a repeated `make up`, the lifecycle ends a still-running root sync only when
Argo has already compared a newer Git revision. This lets Argo restart at the
current revision if an older package wave was blocked by a manifest since fixed
on `main`. The health wait repeats this check because Argo may compare the new
revision after the lifecycle starts waiting.
During readiness checks, brief Kubernetes command failures are retried. Six
consecutive failures still stop the deployment; teardown checks remain fail-closed.

There is no foundations profile or separate ingress deployment step. Configure
packages in `helm_chart/platform/` before applying. SonarQube SSO and application
prerequisites must be configured for the full deployment. Backstage uses the
existing registry and native Entra configuration; Atlantis remains optional.

## Argo CD HTTPS access

The URL is `https://<argocd.dns_label>.<dns.zone.name>`, using the two explicit
values in `bootstrap/terraform.tfvars`. The configured URL is
`https://argocd-management.dev.michaelalinks.com`. The lifecycle prints the URL after
the certificate is ready, DNS resolves to the current Gateway and an HTTPS health
check succeeds with normal certificate verification. The configured ACME email
must be real, and the existing bootstrap identity needs its DNS-zone role and
cert-manager/ExternalDNS federations.

An existing exact A, AAAA or CNAME record blocks seeding unless its ExternalDNS TXT
ownership matches this cluster and Argo route. Existing records are not adopted.
An explicit new hostname takes precedence over a wildcard record. The example
prefix avoids the existing `argocd` entry; recheck your zone before choosing a
custom label.

Argo remains a ClusterIP service. Envoy terminates HTTPS using the per-host
certificate cert-manager generates from Argo's issuer-annotated ListenerSet.
Envoy forwards HTTP to Argo inside the cluster; backend encryption and restricted
workload access remain hardening work. ExternalDNS owns the DNS entry; Terraform
owns the Argo release and its canonical URL. No port forwarding is required.

## Argo CD sign-in

Select **LOGIN VIA MICROSOFT ENTRA ID** on the Argo login page. Bootstrap creates
an Entra application named by `entra_applications.argocd.display_name`, with the public Argo
`/auth/callback` URL and the CLI loopback callback. Members of the bootstrap
`aks.admin_group` receive Argo's built-in `role:admin`; other tenant users can
complete authentication but receive no Argo permissions. Anonymous access is off.
Group membership is distinct from ownership: owning the Entra group alone does
not grant Argo access. Bootstrap includes its authenticated caller as a member.

Argo authenticates its OIDC code exchange using the server's existing projected
service-account token and a dedicated Entra application federation. There is no
client secret to rotate or store in Terraform state. The server keeps its existing
managed-identity annotation for management-to-child Kubernetes authentication.
Argo is part of the initial bootstrap, so Terraform
uses the shared `entra_applications` module call, keyed by the tfvars map, for this app, its service
principal and federation. OIDC settings live in the commented
`bootstrap/argocd-configmap.yaml` template; RBAC lives in `argocd-values.yaml`.
Terraform fills their deployment placeholders from module outputs and context.
The tenant is resolved from authenticated provider context and is not published
as an output or tracked deployment value.

After merging the configuration, run `make plan` then `make up`. The executing
identity needs Entra authority to create applications, service principals and
federated credentials; Azure subscription roles alone do not grant that authority.
This also depends on the tenant permitting the requested OpenID scopes/consent.

Verify an administrator can sign in and sync an application, inspect **User Info**
for the group claim, and verify a tenant user outside that group cannot list or
modify applications. CLI login uses
`argocd login argocd-management.dev.michaelalinks.com --sso --grpc-web`.
Security-group claims have Entra's 200-group token limit; Graph group-overage
resolution is not enabled by this setup. A user without the required claim gets
no group-based access. Do not interpret deployment health as successful SSO testing.

Local `admin` login remains enabled for recovery until interactive SSO is verified.
With a management kubeconfig selected, retrieve the initial password using
`argocd admin initial-password -n argocd`; it is generated by Argo, not a shared
password in tfvars. After changing it, the initial-password Secret is no longer
the current password. Disabling local admin later requires a reviewed change to
`admin.enabled` in `bootstrap/argocd-configmap.yaml`.

Sources: [Argo Entra SSO](https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/microsoft/),
[Argo RBAC](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/),
[Entra group claims](https://learn.microsoft.com/en-us/entra/identity/hybrid/connect/how-to-connect-fed-group-claims).

## Platform service sign-in

The services below use native Microsoft Entra authentication. After merging service changes,
run `make up`; there is no separate identity setup command.

| Service | Native provider | Access configuration |
| --- | --- | --- |
| [Backstage](https://backstage.dev.michaelalinks.com) | Native Entra; object-ID catalog matching | `backstage.users` in platform values |
| [Langfuse](https://langfuse.dev.michaelalinks.com) | Entra OIDC; recovery password login retained | `langfuse.sso.users` in platform values; currently the portfolio owner |
| [SonarQube](https://sonarqube.dev.michaelalinks.com) | Entra SAML | Existing enterprise-app assignments; portfolio owner has `portfolio-sonar-admins` |

Argo installs [Crossplane and its AzureAD provider](helm_chart/platform/packages/crossplane/README.md),
then manages the Langfuse and Backstage identity requests and explicit user assignments.
Crossplane creates the registration and credential and delivers its connection
Secrets directly to the applications. The custom Graph script and Key Vault credential
bridge are removed. Terraform bootstraps only the federated controller identity
and its explicit Graph consent; service settings remain in GitOps.

Changing Langfuse assignments requires only a GitOps merge. Credential expiry is
explicit (180 days); automatic time-based rotation is not configured. Secret
changes trigger a namespace-scoped web restart. The assigned Langfuse Entra user must have
a nonempty profile email; requesting an optional claim does not populate it.
Password recovery remains enabled until that prerequisite and browser login are
verified; then set `langfuse.sso.disableUsernamePassword: true`. Backstage uses
Entra object-ID matching and does not require this email change.
Sonar's retained SAML registration is reused without a second owner; its normal
configuration Job applies and verifies SAML settings. No deployment or browser
login is implied by the PR or CI results.

Entra sign-in does not replace application authorisation. Verify Langfuse
organisation membership and Sonar group permissions after browser login. The
Sonar recovery admin remains available; SDK ingestion and scanner token access
keep their native API authentication.

See [Langfuse authentication and lifecycle](helm_chart/platform/packages/langfuse/README.md)
and [SonarQube SAML](helm_chart/platform/packages/sonarqube/README.md) for access,
credentials, permissions, rotation and verification details.

## Delivery analytics

[Free self-hosted DevLake](helm_chart/platform/packages/devlake/README.md) reuses
PostgreSQL and Grafana and installs with `make up`. The pinned latest non-beta
release is v1.0.2. Crossplane supplies Entra resources; Envoy provides an SSO
overlay while the backend retains Basic authentication. The final route-stage
Job checks OIDC policy acceptance and certificate readiness. No MySQL, extra
Grafana, Azure resource or PVC is added. Production projects and source/incident
data are explicit inputs; bootstrap/management syncs are not production releases.

## Other commands

| Command | Behavior |
| --- | --- |
| `make help` | Show the main lifecycle commands. |
| `make init` | Initialize Terraform without planning or applying. |
| `make down` | Remove platform packages in dependency order, then destroy bootstrap infrastructure and its disposable application data containers. |
| `make validate` | Run lifecycle tests and chart validation; see the configuration guide for Python validation dependencies. |

`make down` deletes disposable application data. The existing storage account,
DNS zone, Key Vault and external Terraform state backend remain. The Argo namespace
is retained until Terraform removes the Argo release and destroys the cluster;
GitOps cleanup must not delete its own controller namespace. Run lifecycle
commands serially.

## Repository guides

- [Bootstrap](bootstrap/README.md): Terraform ownership and initial Argo CD access.
- [Configuration and lifecycle](helm_chart/README.md): private inputs, backend
  setup, prerequisites, validation and teardown behavior.
- [Platform](helm_chart/platform/README.md): package configuration, ownership and
  dependency ordering.
- [Federation](helm_chart/federation/README.md): onboarding independent workload
  roots through the management ApplicationSet.
- [Backstage application](helm_chart/backstage/README.md) and
  [deployment package](helm_chart/platform/packages/backstage/README.md): enabled
  developer portal using the shared PostgreSQL cluster.

Validation covers lifecycle tests, rendered Kubernetes resources and pinned
upstream charts. Live installation and multi-cluster failure behavior have not
yet been validated in this workspace; passing CI does not establish deployment
readiness.

The full root includes [free standalone Testkube](helm_chart/platform/packages/testkube/README.md)
on management and [Argo Rollouts](helm_chart/platform/packages/argo-rollouts/README.md)
on every cluster. `make up` gates their dependencies and runs migration, artifact
and failure checks. Standalone has private Kubernetes-authorized CLI access;
its application SSO/RBAC and cross-cluster gate limits are documented in the guide.
