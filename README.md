# GitOps

Platform GitOps configuration and lifecycle entry point.

## Bootstrap lifecycle

`bootstrap/` contains the Terraform root for the central management AKS cluster
and initial Helm ArgoCD installation. Platform packages will be migrated from
Flux to ArgoCD/Helm afterward. The preserved Flux configuration is on
`feature/migrate-away-from-flux`.

Install Terraform, Azure CLI and kubelogin, then authenticate with `az login`.
Select the account with `az account set --subscription "<subscription-name>"`.
The Makefile reads the selected subscription and tenant into process environment
variables; neither identifier is stored in tfvars.
Complete the marked AKS version, Entra admin group and API CIDR values in
`bootstrap/terraform.tfvars` before running:

```sh
make plan
make up
make down
```

Each target initializes Terraform first. `make up` applies automatically;
`make down` destroys the resources managed by that bootstrap state automatically.
Terraform loads `bootstrap/terraform.tfvars` and keeps state in `bootstrap/`.
The current backend is local: retain the state for updates and teardown.
The existing `dev.michaelalinks.com` DNS zone remains outside bootstrap ownership.

`BOOTSTRAP_DIR` overrides the Terraform root and `TERRAFORM` overrides the executable.
See [bootstrap/README.md](bootstrap/README.md) for infrastructure details and access.

These targets currently manage bootstrap only. Migrating the archived Flux
packages to ArgoCD/Helm is the next increment.

## CI authentication

Use the same Make targets after establishing an Azure CLI session for the CI
identity. For GitHub Actions, authenticate the federated managed identity using
the org secrets (the job needs `id-token: write`):

```yaml
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
- run: make plan
```

The job must also check out this repository and install Terraform and kubelogin.
The identity needs GitHub OIDC federation and permissions for the resources it
manages, including AKS access when Helm runs. An Azure-hosted runner can instead
establish its CLI session with `az login --identity`.

Terraform and kubelogin both use the resulting CLI session, so the Makefile and
tfvars work in either environment. The existing CI workflows perform static
validation only; this snippet shows authentication for a future lifecycle job.
