# Management cluster bootstrap

Provisions the central management AKS cluster and installs ArgoCD through Helm
from one Terraform root and one state. Deployment settings live in
`terraform.tfvars`; input declarations are in `variables.tf`. Related settings
are grouped into `azure`, `network`, `aks`, `dns` and `argocd` objects. Subnets
and user node pools are maps of objects keyed by resource name.

The configuration creates a dedicated resource group, management VNet and subnets,
AKS system/application pools, and an ExternalDNS workload identity. It references
the existing `dev.michaelalinks.com` DNS zone in resource group `kubernetes`,
preserving its delegation and granting the DNS identity access at zone scope.

ArgoCD depends on the AKS module. Helm uses the module's API endpoint and CA
certificate outputs, which wait for AKS and its node pools. `argocd-values.yaml`
contains the initial Helm values. Authentication uses your Entra Azure CLI session
through `kubelogin`; local AKS admin accounts are disabled.

## Run

Run Terraform from `gitops/bootstrap`, or use `make plan`, `make up` and
`make down` from the GitOps repository root.

Install Terraform >=1.5, Azure CLI, kubectl and kubelogin. Authenticate with
`az login`. Your account needs resource creation/role assignment permissions and
membership in the configured Entra admin group.

Complete the three marked inputs under `aks` in `terraform.tfvars`: a supported
exact AKS version, Entra admin group IDs and operator/runner public API access
CIDRs.
The remaining management settings are populated and can be edited there.

```sh
az aks get-versions --location uksouth -o table
terraform init
terraform plan -out=bootstrap.tfplan
terraform apply bootstrap.tfplan
```

Terraform automatically loads `terraform.tfvars`. There are no separate
infrastructure/ArgoCD stages or target-based applies. A fresh cluster deployment
and Helm connection still need verification in a live apply; CI checks formatting,
provider schemas and chart rendering without provisioning Azure resources.

Connect after provisioning:

```sh
export KUBECONFIG="$(mktemp)"
az aks get-credentials --resource-group rg-management --name aks-management
kubelogin convert-kubeconfig -l azurecli
kubectl get nodes
kubectl -n argocd get pods
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Use matching resource/cluster names if overriding the tfvars. ArgoCD is available
at `https://localhost:8080`; its initial admin secret is in
`argocd-initial-admin-secret` in namespace `argocd`. Retrieve it locally and remove
the temporary kubeconfig after use. Never commit credentials.

## State and remaining work

The single root currently uses local state. Protect and retain it; configure an
Azure Blob backend before shared operation. State and saved plans are ignored.
The tracked tfvars contains non-secret settings and marked deployment inputs only.

ExternalDNS and Envoy Gateway controllers, public TLS, GitOps Applications and
ArgoCD self-management will be added through GitOps. This bootstrap prepares the
DNS identity but does not yet expose a public endpoint. Dev/prod clusters follow.

Terraform owns the initial ArgoCD Helm release. Before GitOps takes ownership,
commit matching chart/values and an Application in `gitops`, then explicitly
remove the Helm release from Terraform state and retire its module call. Removing
the call alone would plan an uninstall. Do not give both tools ownership.

For teardown, remove any later GitOps workloads first. Terraform dependencies
order the Helm release before AKS destruction. The existing DNS zone is a data
source and remains outside this state's ownership.
