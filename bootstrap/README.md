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

## Optional Atlantis inputs

`platform.atlantis` defaults to disabled, preserving existing local tfvars. The
GitOps Atlantis package consumes these settings through `platform_settings`.
Enable it only on management after creating its GitHub App and secrets:

```hcl
# Inside platform = { ... }
atlantis = {
  enabled         = true
  github_app_id   = "REPLACE_WITH_NUMERIC_APP_ID"
  github_app_slug = "REPLACE_WITH_APP_SLUG"
  github_team     = "REPLACE_WITH_TRUSTED_TEAM_SLUG"
  repositories = [
    "github.com/YOUR_ORG/*",
    "!github.com/YOUR_ORG/gitops",
    "!github.com/YOUR_ORG/terraform",
  ]
}
```

Organisation patterns automatically cover new Backstage-created repositories;
there is no per-repository Atlantis server update. Exact repository IDs remain
supported, and `!` patterns exclude fixed platform/bootstrap or module-library
repositories. Wildcard host or organisation names are rejected. The GitHub App's
installation must independently grant access to new repositories: install it on
all repositories in the organisation or include installation access in the
repository onboarding workflow. These patterns do not grant Azure permissions.

Atlantis reads secrets from the same `platform.key_vault` used by bootstrap and
External Secrets. No separate Key Vault is created. Add the four secret values
manually to that existing vault as described in the Atlantis package guide; do
not put them in Terraform inputs.

Add a dedicated entry to the existing `identities` map in ignored local tfvars:

```hcl
# Inside identities = { ... }
atlantis = {
  name = "id-management-atlantis"

  federated_credentials = {
    atlantis = {
      namespace       = "atlantis"
      service_account = "atlantis"
    }
  }

  role_assignments = {
    state = {
      scope                = "terraform_state"
      role_definition_name = "Storage Blob Data Contributor"
    }
    deployment = {
      scope                = "developer_environment"
      role_definition_name = "Contributor"
    }
  }
}
```

`identity_role_scopes` adds named scopes to the identity module's existing scope
map. Set `terraform_state` to the persistent state storage account's ID and
`developer_environment` to the approved deployment resource group's ID in ignored
local tfvars. Choose narrower/custom deployment roles where appropriate. The
built-in `dns_zone`, `key_vault` and `storage_account` aliases remain reserved.
No subscription-wide Contributor, Owner or role-assignment privilege is granted
by these examples. The target resource group must already exist; provisioning
shared foundations remains a platform operation.

The dedicated identity gets no access to the management Terraform state unless
you deliberately choose that scope. Use a separate state account when a real
security boundary between management and developer state is required; different
state keys alone do not provide access isolation. This change creates no state
storage account/container and never deletes the existing backend.

GitHub private keys, webhook secrets and UI passwords stay in Key Vault, outside
Terraform state and Helm values. Live GitHub/Azure integration and permission
controls must be verified before use. Bootstrap and recovery remain executable
outside the cluster; do not give Atlantis ownership of its hosting cluster.
