# GitOps

Platform GitOps configuration and lifecycle entry point.

## Bootstrap lifecycle

The Makefile currently runs the sibling `bootstrap` Terraform root, provisioning
the central management AKS cluster and initial Helm ArgoCD installation. Platform
packages will be migrated from Flux to ArgoCD/Helm afterward. The preserved Flux
configuration is on `feature/migrate-away-from-flux`.

Check out `gitops` and `bootstrap` alongside each other. Install Terraform, Azure
CLI and kubelogin, then authenticate with `az login`. Complete the marked version,
Entra admin group and API CIDR values in `bootstrap/terraform.tfvars` before running:

```sh
make plan
make up
make down
```

Each target initializes Terraform first. `make up` applies automatically;
`make down` destroys the resources managed by that bootstrap state automatically.
The existing `dev.michaelalinks.com` DNS zone is referenced by bootstrap and remains
outside its ownership. The commands load `bootstrap/terraform.tfvars` automatically
and leave the state in the bootstrap checkout. Retain that state for later updates
and teardown; the current bootstrap uses a local backend.

Use another checkout location with:

```sh
make up BOOTSTRAP_DIR="/path/to/bootstrap"
```

`TERRAFORM` can override the Terraform executable. These targets currently manage
bootstrap only; they do not deploy or remove the archived Flux packages.
