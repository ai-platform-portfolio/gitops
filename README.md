# GitOps

A federated Argo CD platform on Azure AKS. Each cluster reconciles the shared
platform independently; management onboards workload clusters.

Terraform provisions the management infrastructure and installs its initial
Argo CD Helm release. The platform Helm chart defines the application packages
that Argo CD reconciles, with readiness gates between dependencies. Bootstrap
and platform installation are separate steps so you can bring up AKS and Argo CD
with trusted HTTPS before deploying the applications.

## Before you start

Run the commands below from this repository's root directory. Install Terraform
>=1.5, Azure CLI, kubectl, kubelogin and Python 3. Authenticate with Azure CLI and
select the intended subscription.

Prepare these ignored local configuration files:

1. Copy `bootstrap/terraform.tfvars` to `bootstrap/local.tfvars` and complete the
   deployment inputs, including AKS version, Entra admin group, API access CIDRs,
   existing DNS zone, Key Vault and platform storage account references.
2. Create `bootstrap/backend.local.hcl` for the existing Azure Blob Terraform
   state backend. Use the example in the
   [configuration guide](helm_chart/README.md#remote-terraform-state).

Make automatically selects `bootstrap/local.tfvars` when present. Override paths
with `TFVARS=/path/to/settings.tfvars`, `BACKEND_CONFIG=/path/to/backend.hcl` or
`BOOTSTRAP_DIR=/path/to/bootstrap`. Keep private values and state out of Git.

The initial portfolio configuration uses one Standard_D4s_v5 system node and one
Standard_D4s_v5 application node. See the configuration guide for prerequisites
and limits before applying.

## Bootstrap with Argo HTTPS: plan and apply

Preview the Terraform infrastructure and initial Argo CD installation
(GitOps chart changes are validated separately with `make validate`):

```sh
make plan
```

Apply bootstrap when you are ready:

```sh
make bootstrap
```

Both commands initialize Terraform automatically and use the authenticated Azure
CLI account context. There is no separate `terraform init` or environment-variable
setup to run. `make bootstrap` automatically approves Terraform apply; it computes
its plan at apply time rather than consuming a saved plan from `make plan`.

Bootstrap creates networking, AKS, managed identities, disposable application data
containers and the Argo CD Helm release. It then seeds the existing platform chart
with `profile: foundations`. Argo installs only Envoy Gateway, cert-manager,
ExternalDNS and the shared configuration, namespaces and route needed for its own
HTTPS endpoint. Databases, observability, federation and application packages stay
disabled. Application credentials and SonarQube SSO are not prepared in this mode.

The default URL is `https://argocd-management.<your-platform-domain>`. Override the
prefix with `argocd.dns_label` in your ignored tfvars. The lifecycle prints the URL
only after the certificate is ready, DNS resolves to the current Gateway and an
HTTPS health check succeeds with normal certificate verification. The configured
ACME email must be real, and the existing bootstrap identity needs its DNS-zone
role and cert-manager/ExternalDNS federations.

An existing exact A, AAAA or CNAME record blocks seeding unless its ExternalDNS TXT
ownership matches this cluster and Argo route. Existing records are not adopted.
An explicit new hostname takes precedence over a wildcard record. The default
prefix avoids the existing `argocd` entry; recheck your zone before choosing a
custom label.

Argo remains a ClusterIP service. Envoy terminates HTTPS using the per-host
certificate cert-manager generates from Argo's issuer-annotated ListenerSet.
ExternalDNS owns the DNS entry; Terraform owns the Argo release and its canonical
URL. There is no separate load balancer or ingress controller just for Argo.

On a fresh cluster, stop here if you only want bootstrap and Argo access. Repeating
`make bootstrap` preserves an existing full platform root; it does not switch it
back to foundations or prune application packages. Both modes use the same root
and foundation Applications, so later enablement keeps their ownership.

## Deploy the platform later

```sh
make up
```

This applies Terraform, prepares application Secrets and changes the same root
to `profile: full`, then waits for the platform packages to reconcile. Argo CD automatically applies the enabled packages from
Git after the root is seeded. You do not need a separate Helm install command.

Configure packages in `helm_chart/platform/` before this step. Optional packages
have their own prerequisites; Backstage, for example, stays disabled until its
image and company identity/network settings are supplied.

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
