# GitOps bootstrap

This management-cluster Terraform root provisions networking, AKS, managed
identities, disposable data containers and the initial ArgoCD Helm release.
The platform charts and package lifecycle are added in
[PR #10](https://github.com/ai-platform-portfolio/gitops/pull/10).
Subsequent clusters use their own Terraform roots.

## Configuration

Copy `bootstrap/terraform.tfvars` to ignored `bootstrap/local.tfvars`. Set the
AKS version, Entra admin group, API CIDRs and deployment settings there. Make
passes that file with `-var-file` when present; override it with `TFVARS=/path/to/settings.tfvars`.
Existing DNS, Key Vault and the single platform storage account are looked up by
name. Terraform creates and deletes the configured containers within that account.
Use fresh container names: existing containers must not be silently adopted or deleted.

The initial pools each use one Standard_D4s_v5 node: one system node and one
application node. Increase capacity when measured portfolio usage requires it.
Private tfvars, backend settings and Terraform state stay out of Git.

## Remote Terraform state

The root uses an Azure Blob backend with Microsoft Entra authentication, not
storage account keys. Backend settings are separate from deployment tfvars
because Terraform reads them during `init`, before provider data sources.

Set up ignored `bootstrap/backend.local.hcl` with the existing state storage
account, container and a unique state key:

```hcl
storage_account_name = "YOUR_STATE_ACCOUNT"
container_name       = "tfstate"
key                  = "gitops/management.tfstate"
resource_group_name  = "YOUR_STATE_RESOURCE_GROUP"
```

The selected backend's subscription/tenant can also be set in this ignored file.
Otherwise Make supplies account context from the authenticated CLI session through
`ARM_SUBSCRIPTION_ID` and `ARM_TENANT_ID`. Override the file path with
`BACKEND_CONFIG=/path/to/backend.hcl`. The identity needs Blob Data Contributor
access to the state container for state reads, writes and lease locking.

`make init` passes this file to Terraform. It does not automatically migrate local
state or overwrite another deployment's state; review any migration explicitly.
CI uses `terraform init -backend=false` and does not access live state. The state
account/container are external to this root and survive application teardown.

## Lifecycle

Install Terraform >=1.5, Azure CLI and kubelogin, then authenticate with Azure CLI
and select the intended subscription. CI uses that same session through federated
identity or managed identity.

```sh
make plan
make up
make down
```

This bootstrap installs Argo but does not seed platform Applications. After
installing the platform, use its lifecycle from PR #10 to remove packages before
infrastructure. Teardown is disposable: containers and their data are deleted;
the existing storage account, DNS zone and Key Vault remain outside this root.

Terraform reads account context internally through `azurerm_client_config.current`;
tenant and subscription IDs are not Terraform outputs. Identity configuration is
one object-map loop, with one Blob Data Contributor assignment at storage-account
scope for each identity that accesses storage.

`argocd-values.yaml` contains deployment settings. The actual
`argocd-configmap.yaml` holds documented health and diff customizations. Helm
installs it as part of the Terraform-owned release. Reusable Azure modules remain
in the separate Terraform repository at immutable commits.

No infrastructure is deployed by validation CI. See
[bootstrap/README.md](bootstrap/README.md) for Argo access details.
