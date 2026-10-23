# Atlantis

Management-only Terraform PR execution using official chart **6.16.2**, Atlantis
**v0.48.2**, verified latest stable on 2026-10-23. One replica and a 5 GiB Azure
Disk store BoltDB locks, checkouts and saved plans. No PostgreSQL or Redis is
required. Terraform state stays in the consumer roots' existing Azure Blob backend.

The package requests 100m CPU/256 MiB memory, with a 1 GiB memory limit. Measure
actual Terraform run usage before increasing capacity. The runner defaults to
Terraform 1.17.0; consumer projects should pin tested Terraform/provider versions.

## Ownership and ordering

| Owner | Resources | Wave |
| --- | --- | --- |
| Terraform bootstrap | AKS, initial Argo, ASO provisioner identity/authority and Argo health checks | Before platform |
| namespaces Application | Atlantis namespace | -10 |
| configuration Application | GitHub/UI ExternalSecrets, namespace ASO credential, NetworkPolicy | 10 |
| Azure Service Operator Application | Operator and required identity/role CRDs; watches Atlantis when enabled | 10 |
| atlantis-azure Application | UserAssignedIdentity, FederatedIdentityCredential, scoped RoleAssignments | 20 |
| ASO controller | Exports client/principal IDs into `atlantis-identity` ConfigMap | Before Azure Ready |
| atlantis Application | Runner, service account, disk and internal Service | 40 |
| routes Application | Application-owned ListenerSet, HTTPS route and redirect | 80 |

`azure-resources.yaml` defines the package-owned Azure resources. Their namespace
is `atlantis`, which is added to ASO's watched namespaces only when enabled. ASO
uses the namespace `aso-credential` populated from bootstrap's provisioner identity.
The operator's global credential stays empty. These identity contexts are different:
ASO can create the application identity; Atlantis uses that new identity for runs.

Bootstrap's wildcard Azure health check requires ASO Ready for the resource's
current generation. Individual resource waves create identity, federation and
roles in order; the root waits for all of them before starting the runner. Azure
RBAC propagation can still delay initial access after the control plane reports
Ready; test the initial backend operation and retry after propagation as needed.

ASO writes `clientId` and `principalId` to the ConfigMap in this namespace. The
runner reads client ID through `configMapKeyRef`, projects its own service-account
token with audience `api://AzureADTokenExchange`, and supplies it to both AzureRM
and Blob backend auth. It needs no static client-ID annotation, token-copy Job,
Azure CLI session, extra ConfigMap reflector or Kubernetes backend RBAC.

## Bootstrap and private inputs

[Bootstrap configuration](../../../../bootstrap/README.md#optional-atlantis-and-aso-inputs)
contains complete input examples for GitHub, the existing identity resource group,
state/deployment role targets and `identities.aso`. Merge both Atlantis PRs before
enabling it. Older Terraform outputs without Atlantis settings leave it disabled;
workload clusters omit all Atlantis resources even if the flag is supplied.

The service-identity resource group and target state account/deployment resource
groups must already exist. All targets use the selected subscription. Roles are
configured as a map of resource group, optional storage account and built-in role
name. Storage roles use account scope. Names produce stable Azure role-assignment
GUIDs during rendering. No new storage account or Key Vault is created.

The provisioner needs identity-management permission on the service-identity group
and role-administration permission on each target scope. Terraform's identity module
owns those initial permissions; ASO owns the application's grants. This is real
role-administration authority, not an implemented tenant permission model. Keep
package/ASO credential namespace writes limited to trusted platform operators until
that model is designed and tested. The pinned ASO release uses its controller's
projected token; do not assume newer strict per-namespace token isolation.

Do not configure `identities.atlantis` in Terraform. Bootstrap rejects mixed ownership.
Existing Terraform-owned foundations and the persistent backend are not adopted
by ASO. If an earlier draft was manually applied, review the state/resource ownership
transition first; this implementation does not automatically detach or import it.
The lifecycle privately reads the AKS OIDC issuer at runtime; it is not a new
Terraform output. Bootstrap and lifecycle still use the current Azure CLI login.

## Existing Key Vault and GitHub App

Use the **same Key Vault selected by `platform.key_vault` in bootstrap**. Its URI
feeds the existing `ClusterSecretStore/azure-kv`, used by Atlantis's ExternalSecrets.
You can manually add these values in the vault, preserving the PEM line breaks:

| Key Vault secret | Purpose |
| --- | --- |
| `atlantis-github-app-key` | GitHub App private key |
| `atlantis-github-webhook-secret` | Random webhook signing secret |
| `atlantis-web-username` | Web UI basic-auth username |
| `atlantis-web-password` | Independent strong UI password |

The namespace ASO credential contains public identity/account context, not a client
secret. Its bootstrap-managed identity authenticates through the ASO controller's
token. GitHub credentials remain external inputs; Azure Service Operator does not
create a GitHub App or obtain its private key.

Create an organisation-owned GitHub App with the upstream
[required permissions](https://www.runatlantis.io/docs/access-credentials#permissions),
including organisation Members read for team checks. Set its webhook to
`https://atlantis.<DNS_ZONE>/events`, enable TLS verification and use the vault's
webhook secret. Subscribe to pull requests, issue comments, pull-request reviews
and pushes. Use one App installation and avoid duplicate repository webhooks.

To cover new Backstage-created repositories, install the App on all repositories
in the approved organisation, or automate installation access during onboarding.
An Atlantis pattern does not itself grant the App GitHub access. API permissions
and repository installation scope are separate choices.

```hcl
repositories = [
  "github.com/ai-platform-portfolio/*",
  "!github.com/ai-platform-portfolio/gitops",
  "!github.com/ai-platform-portfolio/terraform",
]
```

Organisation patterns avoid per-application server updates. Fixed exclusions keep
hosting/bootstrap and module-library repositories outside execution; the App may
still read a private module repository for cloning. Exact repo IDs also work.
[Atlantis syntax](https://www.runatlantis.io/docs/server-configuration#repo-allowlist)
supports these patterns and exclusions.

After secret rotation, wait for ESO Ready, drain runs and restart the StatefulSet
so environment credentials refresh. Secret updates alone do not restart Atlantis.
The UI uses basic auth; commands use GitHub team checks and webhook signatures.
The command API is not enabled.

## Backstage and human review

Backstage can generate catalog metadata, consumer deployment configuration and an
explicit project file in a new organisation-owned repository. Template actions
and status integration belong to the separate Backstage implementation. This PR
provides its execution path; it does not implement automatic Backstage tasks.

A consumer repository composes pinned Terraform modules and contains a provider
lock file plus backend configuration available to the runner. The `terraform`
repository is a module library, not a deployment root. Do not add
`gitops/bootstrap` as a project or give the runner unintended management-state
access. Bootstrap/recovery must remain independently executable outside AKS.

Example consumer repository `atlantis.yaml`:

```yaml
version: 3

automerge: false
autodiscover:
  mode: disabled

projects:
  - name: service-dev
    dir: environments/service-dev
    workspace: default
    terraform_version: v1.17.0
    autoplan:
      enabled: false
```

1. Backstage or an engineer opens a PR for an approved consumer project.
2. A trusted team member comments `atlantis plan -p service-dev`.
3. Review the plan, checks and owner requirements, then approve the PR.
4. A trusted team member comments `atlantis apply -p service-dev`.
5. Merge after successful apply; check application readiness through Argo.

Autoplan and fork/draft handling are disabled. Applies require approval,
mergeability and an undiverged base. Repository custom workflows and requirement
overrides are disabled; unselected bulk apply is disabled. PR locks complement
Blob state leases. Apply uses the saved plan and happens before merge. Undoing an
apply requires another reviewed plan; a Git revert alone changes no Azure resources.

Configure branch protection/rulesets separately: required CI/owner reviews,
stale-review dismissal and deliberate bypass/self-approval rules. Do not require
Atlantis's apply check before apply, which creates a mergeability cycle. The
server shares its identity and credentials across runs; even plan access permits
code execution. It is a trusted-team runner, not isolated untrusted execution.

For future self-service, Backstage may generate ASO manifests for Argo or Terraform
projects for Atlantis. Give every Azure resource one owning engine. GitHub-hosted
plans/comments/logs must fit the company boundary; self-hosting the runner alone
does not establish that boundary. NetworkPolicy allows gateway ingress, DNS and
HTTPS egress, without an FQDN allowlist.

## Teardown and verification

Drain active runs before upgrades or `make down`. The preStop hook waits for
Terraform/Tofu processes up to the 300-second termination grace period; longer
runs or forced termination can still be interrupted. Treat the disk as sensitive.
Disk loss requires new plans and lock review, while remote Terraform state survives.

Cleanup permits only exact Azure CRs listed in the platform-managed
`atlantis-azure` Application's inventory. Any unrelated or untracked Azure CR still
blocks teardown before the platform stops. Reverse waves delete the runner, its
roles/federation/identity, and only then ASO and the namespace credential. ASO and
its bootstrap authority remain available for finalizers; deletion errors prevent
Terraform destroy. Externally provisioned developer infrastructure and persistent
Terraform state are not deleted by removing Atlantis.

Validation covers the identity/resource graph, role scopes and deterministic
names, ConfigMap handoff, projected token, namespace credential/watch settings,
current/stale health, teardown ownership and all pinned charts. These offline
checks do not prove Azure provisioning, RBAC propagation or live self-service.
Before real use, test allowed/denied commands, stale reviews, conflicting PRs,
create/update/delete, token/secret rotation, restart between plan/apply, partial
failure recovery and complete teardown with ASO finalizers.
