# Management cluster bootstrap

One Terraform root builds networking, AKS, map-driven managed identities,
disposable data containers and the initial ArgoCD Helm release. Subsequent
clusters have independent roots. Inputs belong in tfvars; declarations stay in
`variables.tf`, resource lookups and scope aliases in `main.tf`, authentication
in `provider.tf`.

`argocd-values.yaml` holds deployment settings. `argocd-configmap.yaml` is the
actual commented Argo resource installed by the same Helm release. The module
adds the managed identity's client ID to the configured Argo service accounts.

Azure CLI supplies authenticated account context. Tenant/subscription identifiers
are used internally without Terraform outputs. Existing DNS, Key Vault and the
platform storage account are looked up; this root owns their new data containers.
Those containers and their contents are deleted on teardown. There is no
cross-teardown restore or retained-credential store.

Use Make from the repository root. Once platform packages are installed, use its
package cleanup before destroying infrastructure. Protect local Terraform state
and keep private tfvars out of Git. See the [root guide](../README.md).

After obtaining cluster credentials, access Argo with:

```sh
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Read the initial password locally from `argocd-initial-admin-secret`; do not
commit it or print it in logs.
