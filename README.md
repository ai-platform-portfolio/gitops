# GitOps

Platform GitOps configuration and lifecycle entry point.

## Bootstrap lifecycle

`bootstrap/` contains the Terraform root for the central management AKS cluster
and initial Helm ArgoCD installation. Platform packages will be migrated from
Flux to ArgoCD/Helm afterward. The preserved Flux configuration is on
`feature/migrate-away-from-flux`.

Install Terraform, Azure CLI and kubelogin, then authenticate with `az login`.
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
