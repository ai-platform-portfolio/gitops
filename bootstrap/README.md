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

## Optional Atlantis and ASO inputs

Atlantis defaults to disabled. Enable it on management with private GitHub and
Azure deployment choices in ignored local tfvars. The GitOps package owns the
application's managed identity, federated credential and scoped role assignments
through ASO. Terraform supplies ASO's initial authority and the Argo health gate.

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

  azure = {
    # Existing dedicated service-identity RG; keep platform/bootstrap identities
    # in their own Terraform-owned RG. No new storage account is created here.
    identity_resource_group_name = "rg-service-identities"

    role_assignments = {
      state = {
        resource_group_name  = "rg-developer-state"
        storage_account_name = "REPLACE_WITH_EXISTING_STATE_ACCOUNT"
        role_definition_name = "Storage Blob Data Contributor"
      }
      deployment = {
        resource_group_name  = "rg-service-dev"
        role_definition_name = "Contributor"
      }
    }
  }
}
```

An organisation pattern covers Backstage-created repositories without a server
change. Exact repositories and fixed `!` exclusions are also supported. The
GitHub App installation must cover new repos independently of this allowlist.
Use the existing `platform.key_vault` for the four manually supplied GitHub/UI
secrets; never put their values into tfvars or Terraform state.

Add a dedicated provisioner to the existing `identities` map. Its federated
subject is the ASO controller, not the Atlantis service account:

```hcl
# Inside identities = { ... }
aso = {
  name = "id-management-aso-provisioner"

  federated_credentials = {
    aso = {
      namespace       = "azureserviceoperator-system"
      service_account = "azureserviceoperator-default"
    }
  }

  role_assignments = {
    identity_management = {
      scope                = "atlantis_identities"
      role_definition_name = "Managed Identity Contributor"
    }
    state_grants = {
      scope                = "atlantis_state"
      role_definition_name = "Role Based Access Control Administrator"
    }
    deployment_grants = {
      scope                = "atlantis_deployment"
      role_definition_name = "Role Based Access Control Administrator"
    }
  }
}
```

`atlantis_identities` scopes identity management to the configured existing
service-identity resource group. `atlantis_<key>` scopes role administration to
that key's existing resource group or storage account. These aliases are derived
from the same package inputs and authenticated provider context; do not duplicate
ARM scope IDs in output settings. Add matching provisioner authority when adding
package role targets. The state example uses storage-account scope, not individual
containers. All resources must be in the selected subscription for this initial
implementation.

Role administration is privileged within its configured scopes. Review those
scopes and Azure RBAC conditions as part of the permission model. The example
provides no subscription-wide Owner/Contributor and does not let ASO modify
bootstrap identities in the management resource group. Select the state scope as
an explicit trust decision: account-level Blob access
covers all its state blobs, so sharing management/developer state in one account
shares that boundary. A repository exclusion alone does not isolate state. The
external persistent state backend is never owned or deleted by the package.

Do not add an `identities.atlantis` entry: the output precondition rejects mixed
ownership. If an earlier draft was manually applied, perform a reviewed ownership
migration before enabling the new package; this change does not detach existing
state or adopt an existing Azure identity automatically.

Merge the companion GitOps package before enabling the input. Bootstrap installs
current-generation ASO health checks in the actual Argo ConfigMap. The lifecycle
reads the management AKS OIDC issuer privately at runtime; tenant/subscription IDs
and the issuer are not added to Terraform outputs. Bootstrap/lifecycle continue
to use the current CLI login; the Atlantis runner uses its projected token.
No GitHub secrets or Azure resources are provisioned by validation alone.
