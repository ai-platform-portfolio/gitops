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

The application VM size is a capacity estimate; validate it for your workloads.
Private tfvars and Terraform state stay out of Git. State is currently local.

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
