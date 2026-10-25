# Atlantis

Terraform PR execution on each enabled cluster, installed and configured through GitOps.
Official chart **6.16.2** / Atlantis **v0.48.2** was verified latest stable when
added. One replica and a 5 GiB Azure Disk store BoltDB locks, checkouts and saved
plans. No PostgreSQL, Redis, additional Key Vault or state account is required.

**There is no Atlantis Terraform root, tfvars setting, Terraform identity resource
or Terraform output handoff.** Configure the `atlantis` object in
`helm_chart/platform/values.yaml`, open a reviewed GitOps PR, and Argo/ASO reconcile
it. Existing AKS, Argo, ASO, the platform Key Vault and persistent state backend
are platform foundations; this package does not recreate or adopt them.

## Package configuration

Enable the package and supply non-secret GitOps choices:

```yaml
atlantis:
  enabled: true
  github_app_id: "REPLACE_WITH_NUMERIC_APP_ID"
  github_app_slug: platform-atlantis
  github_team: platform
  repositories:
    - github.com/ai-platform-portfolio/cluster-deployments

  azure:
    location: uksouth
    # ASO owns and deletes this group. Use a fresh package-only name.
    identity_resource_group_name: rg-{{ .Values.cluster.name }}-atlantis
    role_assignments:
      state:
        scope: storage_account
        resource_group_name: rg-developer-state
        storage_account_name: REPLACE_WITH_EXISTING_STATE_ACCOUNT
        role_definition_name: Storage Blob Data Contributor
      deployment:
        scope: subscription
        role_definition_name: Contributor
      delegation:
        scope: subscription
        role_definition_name: Role Based Access Control Administrator
      api-allowlist:
        scope: key_vault
        resource_group_name: kubernetes
        key_vault_name: localdevenv
        role_definition_name: Key Vault Secrets User
```

The existing target state account, API allowlist vault and deployment scope must be approved for cluster
creation. The generated Terraform root creates its own resource group and AKS
network role assignments, so it needs subscription-scoped resource creation and
role-assignment authority. These are powerful privileges: keep this dedicated
runner limited to the `cluster-deployments` repository, require trusted approval
before **plan** as well as apply, enforce branch protection/CODEOWNERS and review
the identity's Azure scope before enabling it. A custom Azure role and/or policy
can narrow the built-in roles after proving the required actions. Roles may use
subscription, resource-group or storage-account scope; never use per-container
assignments. Account-level Blob access covers all blobs in that account; sharing
management/developer state in one account shares that trust boundary. Repository
exclusions do not isolate state. All targets use the existing cluster runtime's
subscription context. Keep company deployment choices in an approved private Git
source; do not copy private settings into this public portfolio.

The existing platform lifecycle reads generic account context and each cluster's
AKS OIDC issuer, storing them in the local Argo root's runtime values. No issuer
or account identifiers are added to Terraform outputs. This context is reusable
by GitOps identity consumers; Atlantis enablement is never taken from Terraform.
If a cluster was seeded with an earlier lifecycle, refresh its root once with
`python3 helm_chart/scripts/cluster.py up --infra-dir bootstrap` using the existing
initialized state. This reads existing infrastructure outputs and reconciles the
platform without Terraform apply. Later package changes are ordinary GitOps PRs.

## Dependencies owned by ASO

| Stage | Owner/resources |
| --- | --- |
| Namespace, wave -10 | Package namespace |
| Configuration, wave 10 | Key Vault-backed App/UI ExternalSecrets, runtime ASO credential, NetworkPolicy and scoped readiness RBAC |
| ASO, wave 10 | Existing operator, watching Atlantis only when enabled |
| atlantis-azure, wave 20 | New package ResourceGroup, UserAssignedIdentity, FederatedIdentityCredential and configured RoleAssignments |
| Atlantis, wave 40 | Runner, service account, disk, Service and read-only readiness init container |
| Routes, wave 80 | Application-owned ListenerSet, HTTPS route and redirect |

ASO creates the group, identity, federation and grants from `azure-resources.yaml`.
Their owner/reference relationships let ASO resolve dependencies. ASO exports
`clientId` and `principalId` into `ConfigMap/atlantis-identity` in the same namespace.
The runner reads client ID through `configMapKeyRef` and explicitly projects its
Azure-audience token. No static identity annotation, patch Job or reflector is used.
Terraform does not own these resources. Never select an existing/shared resource
group name: ASO can adopt an existing Azure resource, and package deletion would
then put its contents at risk.

The pinned official kubectl 1.35 init container waits for Ready on every declared
Azure resource. Native `kubectl wait` checks ASO's observedGeneration against the
resource generation, so stale readiness does not release startup. A configuration
hash reruns this gate when role/federation inputs change. Only this init container
mounts a Kubernetes API token; its namespace Role permits named-resource reads and
watches, no writes or Secret reads. Terraform runs receive no Kubernetes credential.
The main container's separate token is for Azure authentication.

This gate needs no Terraform-installed Argo customization. It covers startup and
configuration rollouts; it is not continuous ASO health monitoring. Inspect ASO
conditions for later Azure drift/errors. RBAC propagation can also lag Ready;
verify the first real backend operation before accepting the deployment.

## ASO's initial authority and existing Key Vault

ASO must already have an Azure identity authorised to provision package resources.
An operator cannot grant itself initial Azure access. This is a one-time platform
prerequisite, not an Atlantis Terraform dependency. The package does not create
that initial authority or change its permissions.

For the pinned ASO controller-token model, that identity must trust its cluster's
AKS OIDC issuer with subject
`system:serviceaccount:azureserviceoperator-system:azureserviceoperator-default`
and audience `api://AzureADTokenExchange`. Its approved permissions must cover
creating the package resource group/managed identity and managing role assignments
on the specified state/deployment scopes. Resource-group creation requires parent
subscription authority; review that authority and policy before use. The actual
permission model and live permission checks are not established by this package.

The existing provisioner's client ID comes from the management root runtime
context. GitOps creates the namespace `aso-credential` with that ID and the
runtime tenant/subscription context; the operator uses its projected token.
The ASO global credential stays empty.
Only trusted platform operators should write this namespace/credential; the pinned
ASO release does not provide strict per-namespace token isolation.

Manually add the GitHub/UI secrets to that same vault:

| Key Vault secret | Purpose |
| --- | --- |
| `atlantis-github-app-key` | GitHub App private key, preserving PEM line breaks |
| `atlantis-github-webhook-secret` | Random webhook signing secret |
| `atlantis-web-username` | UI basic-auth username |
| `atlantis-web-password` | Independent strong UI password |

ASO does not obtain GitHub credentials. After rotation, wait for ESO Ready, drain
runs and restart Atlantis so environment credentials refresh. Secret updates alone
do not restart its process. The command API is not enabled.

## Backstage and human review

Create an organisation-owned GitHub App with the upstream
[required permissions](https://www.runatlantis.io/docs/access-credentials#permissions),
including Members read for trusted-team checks. Configure the App webhook at
`https://atlantis.<DNS_ZONE>/events`, with TLS verification and the vault's signing
secret. Subscribe to pull requests, issue comments, pull-request reviews and pushes.
Use one App installation and avoid duplicate repository webhooks.

Install the App on `cluster-deployments` and give it read access to the shared
module library. The server allowlist accepts only the dedicated consumer repo;
management bootstrap and the module library remain outside Atlantis execution.

The Backstage cluster template opens a PR containing `clusters/<name>` with a
pinned module, backend key and reviewed tfvars. Explicit `-d` commands select its
directory; the server disables autoplan. The `terraform` repo is a module
library, and `gitops/bootstrap` remains outside Atlantis execution.

1. Backstage or an engineer opens a consumer PR.
2. A trusted team member reviews and approves it, then comments `atlantis plan -d clusters/<name>`.
3. Review the plan/checks and reconfirm the approval is current.
4. A trusted team member comments `atlantis apply -d clusters/<name>`.
5. Merge after successful apply; inspect application readiness through Argo.

Autoplan, fork/draft handling, custom repository workflows and requirement overrides
are disabled. Plans and applies require approval, mergeability and an undiverged base; bulk
apply without project selection is disabled. Configure branch protection/owner
reviews and stale-review dismissal separately. Do not require the apply status
before apply, which creates a mergeability cycle. The shared runner is for trusted
teams: even plan access permits code execution. Reverting Git alone does not undo
an apply; execute another reviewed plan.

For future self-service, Backstage may generate ASO manifests for Argo or Terraform
projects for Atlantis. Each Azure resource has one owning engine. GitHub-hosted
plans/comments/logs must fit the company boundary; self-hosting the runner does
not establish it. NetworkPolicy permits gateway ingress, DNS and HTTPS egress,
without an FQDN allowlist.

## Teardown and acceptance

Drain runs before upgrades or teardown. The preStop hook waits for Terraform/Tofu
up to the 300-second grace period; forced or longer-running termination can still
interrupt a run. Treat the disk as sensitive. Losing it requires fresh plans and
lock review, while remote Terraform state survives.

Cleanup permits only exact Azure CRs in the platform-managed `atlantis-azure`
Application inventory; unrelated/untracked CRs still block before the platform
stops. Reverse waves remove the runner, roles, federation, identity and its group
before ASO and the namespace credential. ASO's initial platform authority must
remain available until all finalizers finish. Removing Atlantis does not delete
the existing state backend or externally provisioned developer infrastructure.

Offline validation covers GitOps inputs, resource graph, stable names, credential
sources, readiness/token/RBAC scope, teardown and all pinned charts. It does not
prove live Azure permissions, propagation or finalizers. Exercise approved/denied
commands, stale reviews, concurrent PRs, create/update/delete, restart between
plan/apply, token/secret rotation, partial failure and complete teardown before use.
