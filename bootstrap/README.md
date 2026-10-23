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
package cleanup before destroying infrastructure. State uses the external Azure Blob backend configured by ignored
`backend.local.hcl`; keep backend details and private tfvars out of Git. See the [root guide](../README.md).

After obtaining cluster credentials, access Argo with:

```sh
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Read the initial password locally from `argocd-initial-admin-secret`; do not
commit it or print it in logs.

## Optional Backstage inputs

`platform.backstage` passes deployment configuration to the platform lifecycle;
it creates no Azure resources. It defaults to disabled. Enabling requires a built
immutable image (`registry/portal@sha256:<64 hex characters>`) and management role.
Keep company endpoint details in ignored `local.tfvars`:

```hcl
backstage = {
  enabled = true
  image   = "registry.company.internal/platform/backstage@sha256:REPLACE_WITH_IMAGE_DIGEST"
  egress = [{
    to    = [{ ipBlock = { cidr = "10.20.0.10/32" } }]
    ports = [{ protocol = "TCP", port = 443 }]
  }]
}
```

This object belongs inside `platform`. Allow only approved identity and repository
endpoints. Kubernetes NetworkPolicy IP rules are not hostname enforcement and
must account for the cluster CNI's NAT behaviour; verify the effective path live.
No new PostgreSQL instance, storage account, identity or role assignment is needed.
The separate Backstage platform PR consumes this optional output; existing platform
code ignores it. It reuses CNPG, ESO/Key Vault and the existing Gateway.
