# GitOps bootstrap

This Terraform root provisions AKS, networking, managed identities and the initial
ArgoCD Helm release. Bootstrap stays in `bootstrap/`; reusable Azure modules come
from the separate Terraform repository at immutable commits.

The platform charts and backup-aware package lifecycle are introduced separately
in [PR #10](https://github.com/ai-platform-portfolio/gitops/pull/10).

## Configuration

Copy `bootstrap/terraform.tfvars` to ignored `bootstrap/local.tfvars` and fill in
the deployment settings. In particular, choose a supported exact AKS Kubernetes
version, the Entra admin security group and authorized public API CIDRs. Existing
DNS, Key Vault and one retained platform storage account must already exist.
The retained account uses separate cluster data containers and metadata prefixes.

The Makefile uses `bootstrap/local.tfvars` for management when present and passes
it with `-var-file`. Set `TFVARS=/path/to/settings.tfvars` to choose another file.
Workloads use `CLUSTER=<name>`, the matching Terraform workspace and
`clusters/<name>/terraform.tfvars`; start from `clusters/workload.tfvars.example`.
Management uses workspace `default`. Private tfvars and state must stay out of Git.

## Bootstrap lifecycle

Install Terraform >=1.5, Azure CLI, Python 3 and kubelogin (used by the Helm
provider). Authenticate with Azure CLI and select the intended subscription.
Local users and CI use that same authentication path; CI can establish its CLI
session through federated identity or managed identity.

```sh
make plan
make up
```

`make up` prepares missing containers in the retained account, then provisions
AKS and Argo through one Terraform root. It does not seed platform Applications.
`scripts/prepare_storage.py` reads the grouped storage input and creates missing
containers; `scripts/azure.py` provides shared CLI account context.

The `identities` map configures federated service accounts and scoped roles through
one reusable identity module loop. Terraform reads its tenant internally from
`azurerm_client_config.current`; tenant/subscription IDs are not Terraform outputs.

Terraform owns Argo's Helm release. `bootstrap/argocd-values.yaml` holds deployment
settings; the commented `bootstrap/argocd-configmap.yaml` provides custom health
checks and diff rules for the subsequent platform app-of-apps deployment.

Before installing platform workloads, `make down` can remove this bootstrap.
Once workloads or databases are installed, use the platform lifecycle in PR #10
for ordered teardown and verified backups; direct Terraform destruction bypasses
those safeguards. Containers and their storage account are outside this root's
ownership and survive teardown.

State is currently local. Protect it and run lifecycle commands serially.
CI validates Terraform and renders the pinned Argo chart; no infrastructure is
deployed by validation. See [bootstrap/README.md](bootstrap/README.md) for details.
