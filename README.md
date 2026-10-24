# GitOps

A federated Argo CD platform on Azure AKS. Each cluster reconciles the shared
platform independently; management onboards workload clusters.

Terraform provisions the management infrastructure and installs its initial
Argo CD Helm release. The platform Helm chart defines the application packages
that Argo CD reconciles, with readiness gates between dependencies. One Make
command applies the bootstrap and reconciles the full platform,
including Argo CD access through Envoy and a per-host HTTPS certificate.

## Before you start

Run the commands below from this repository's root directory. Install Terraform
>=1.5, Azure CLI, kubectl, kubelogin and Python 3. Authenticate with Azure CLI and
select the intended subscription.

Review the deployment configuration and configure the state backend:

1. Review `bootstrap/terraform.tfvars`, including the AKS version, admin-group
   members, API CIDRs and existing Azure resource references. This tracked
   file is the deployment configuration; complete the API allowlist before use.
2. Create `bootstrap/backend.local.hcl` for the existing Azure Blob Terraform
   state backend. Use the example in the
   [configuration guide](helm_chart/README.md#remote-terraform-state).

Terraform creates the Entra security group described by `aks.admin_group` and
passes its object ID directly to AKS. The authenticated Terraform principal owns
the group through `data.azurerm_client_config.current.object_id`. Configure admin
members in `bootstrap/terraform.tfvars`; ownership alone does not grant cluster
access. The caller needs Microsoft Graph permissions to manage the group. No Azure role
assignments are granted to it. The group and its managed memberships are removed
with this disposable cluster on `make down`.

Make uses `bootstrap/terraform.tfvars`. To select another environment explicitly,
set `TFVARS=/path/to/settings.tfvars`; files named `local.tfvars` have no special
priority. Override the backend or root with `BACKEND_CONFIG=/path/to/backend.hcl`
or `BOOTSTRAP_DIR=/path/to/bootstrap`.

Principal IDs, API CIDRs, resource names and the ACME email are environment
metadata, so they can live in the tracked deployment configuration. Credentials,
private keys, Terraform state and backend configuration stay out of Git.

The initial portfolio configuration uses one Standard_D4s_v5 system node and one
Standard_D4s_v5 application node. See the configuration guide for prerequisites
and limits before applying.

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
Envoy Gateway, cert-manager, ExternalDNS, databases, observability and the enabled
applications are included. Packages configured as disabled remain disabled.

There is no foundations profile or separate ingress deployment step. Configure
packages in `helm_chart/platform/` before applying. SonarQube SSO and application
prerequisites must be configured for the full deployment; optional packages such
as Backstage have their own prerequisites.

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
  [deployment package](helm_chart/platform/packages/backstage/README.md): optional
  developer portal using the shared PostgreSQL cluster.

Validation covers lifecycle tests, rendered Kubernetes resources and pinned
upstream charts. Live installation and multi-cluster failure behavior have not
yet been validated in this workspace; passing CI does not establish deployment
readiness.
