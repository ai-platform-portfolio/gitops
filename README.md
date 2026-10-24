# GitOps

A federated Argo CD platform on Azure AKS. Each cluster reconciles the shared
platform independently; management onboards workload clusters.

Terraform provisions the management infrastructure and installs its initial
Argo CD Helm release. The platform Helm chart defines the application packages
that Argo CD reconciles, with readiness gates between dependencies. Bootstrap
and platform installation are separate steps so you can bring up AKS and Argo CD
before deploying the applications.

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

## Bootstrap only: plan and apply

Preview the infrastructure and initial Argo CD installation:

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
containers and the Argo CD Helm release. **It does not seed the platform chart or
install platform packages.** On a fresh cluster, stop here if you only want the
bootstrap. Running bootstrap again does not disable packages already installed.

## Deploy the platform later

```sh
make up
```

This applies bootstrap first, then prepares cluster credentials and Secrets,
seeds the platform root Application into Argo CD and waits for the platform
packages to reconcile. Argo CD automatically applies the enabled packages from
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
DNS zone, Key Vault and external Terraform state backend remain. Run lifecycle
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
