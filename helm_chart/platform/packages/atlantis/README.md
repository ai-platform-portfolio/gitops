# Atlantis

Management-only Terraform pull-request execution, installed by Argo CD using the
official Atlantis chart **6.16.2** (Atlantis **v0.48.2**). This was the latest
stable chart when verified on 2026-10-23. The package is opt-in until its GitHub
App, secrets and dedicated Azure identity are configured.

Atlantis needs no PostgreSQL or Redis here. One 5 GiB Azure Disk stores BoltDB
locks, repository checkouts and saved plans. Terraform state remains in each
consumer root's persistent Azure Blob backend. One replica requests 100m CPU and
256 MiB memory, with a 1 GiB memory limit; measure actual Terraform run usage.
Terraform defaults to 1.17.0, verified as the latest stable release when added;
consumer projects should pin their tested version and provider lock file.

## Ownership and inputs

- `templates/atlantis.yaml`: pinned Helm Application, wave 40.
- `namespace.yaml`: management namespace, installed by the namespaces Application.
- `secrets.yaml`, `network-policy.yaml`: prerequisites in configuration, wave 10.
- `values.yaml`: runner settings, approval gates and workload identity.
- `networking.yaml`: application-owned ListenerSet and routes, wave 80.

The existing External Secrets controller reads the same platform Key Vault.
cert-manager generates the certificate for `atlantis.<DNS_ZONE>` from the
ListenerSet issuer annotation. There is no extra ingress controller, public
LoadBalancer, wildcard certificate or PostgreSQL dependency.

Supply `platform.atlantis` and a dedicated `identities.atlantis` entry in ignored
Terraform inputs, following the optional Atlantis section in
[bootstrap](../../../../bootstrap/README.md). The bootstrap input PR and this
package PR are independent changes; both are needed for the Make-driven input
handoff. `cluster.py` passes the settings from Terraform to the root Application.
Old outputs without Atlantis settings continue to work with Atlantis disabled.
Workload clusters omit the server, namespace, credentials and routes even if the
input flag is enabled.

## GitHub App and Key Vault prerequisites

Create an organisation-owned GitHub App. For automatic Backstage repository
onboarding, install it on all repositories in the approved organisation. If
installation is limited to selected repositories, the onboarding workflow needs
to grant App access for each newly created repository; an Atlantis wildcard alone
does not grant GitHub access. Configure its webhook as
`https://atlantis.<DNS_ZONE>/events`, with
TLS verification and the same random webhook secret stored in Key Vault. Use
one installation for this server. Subscribe to pull requests, issue comments,
pull-request reviews and pushes, as described by the upstream setup guide.
Do not add a duplicate repository webhook when using the App webhook.

Follow the upstream [GitHub App permissions](https://www.runatlantis.io/docs/access-credentials#permissions)
for the pinned release, including organisation Members read access for team
checks. Review requested API permissions separately from repository installation
scope.
Supply the numeric App ID, App slug, trusted team slug and repository patterns
in local tfvars. Use an organisation pattern so the server configuration stays
stable as Backstage creates repositories:

```hcl
repositories = [
  "github.com/ai-platform-portfolio/*",
  "!github.com/ai-platform-portfolio/gitops",
  "!github.com/ai-platform-portfolio/terraform",
]
```

Exact repository IDs also work. The fixed exclusions keep this hosting/bootstrap
repository and module library out of execution; they are not a growing list of
application repositories. Other organisations/hosts remain excluded. Kubernetes,
Azure scopes and human apply gates still control what accepted requests can do.
[Atlantis allowlist syntax](https://www.runatlantis.io/docs/server-configuration#repo-allowlist)
supports organisation wildcards and exclusions.

You can add these four secrets manually in the **existing bootstrap Key Vault**.
The reference chain is `platform.key_vault` in ignored tfvars → Terraform runtime
vault URI → `ClusterSecretStore/azure-kv` → the Atlantis ExternalSecrets. No new
vault, secret-writing identity or generated GitHub App is needed. Keep the
private key's PEM line breaks and enter values outside Git:

| Key Vault secret | Kubernetes target | Purpose |
| --- | --- | --- |
| `atlantis-github-app-key` | `atlantis-github/key.pem` | App private key, including PEM newlines |
| `atlantis-github-webhook-secret` | `atlantis-github/github_secret` | Webhook signature verification |
| `atlantis-web-username` | `atlantis-web/username` | UI basic authentication |
| `atlantis-web-password` | `atlantis-web/password` | Strong independent UI password |

The UI credentials protect the Atlantis web UI, not Terraform authorisation.
GitHub webhooks use signature verification. Terraform commands are restricted to
the configured GitHub team. The API is not enabled. After secret rotation, wait
for ESO Ready, drain active runs and restart the StatefulSet so environment
credentials are refreshed; a Secret update alone does not restart the process.

## Azure execution and consumer repositories

The service account `atlantis/atlantis` uses its own Azure managed identity and
AKS OIDC federated credential. AzureRM provider and Blob backend authentication
use the projected token via `ARM_OIDC_TOKEN_FILE_PATH`, with Azure CLI auth
disabled. Tenant/subscription context is supplied internally by the existing
lifecycle; no account identifier outputs or checked-in deployment IDs are added.
This configures native auth for Atlantis consumer roots; the existing bootstrap
Make/provider/lifecycle still use Azure CLI.

Grant the identity only the approved deployment scopes and state account access.
The Pod gets no Kubernetes backend RBAC and no ordinary Kubernetes service-account
token. The UI/GitHub credentials and Azure identity are shared by this server's
runs: this is a trusted-team runner, not isolated execution for untrusted tenants.
Even `terraform plan` can execute code, so manual plan access is privileged.

The `terraform` repository is a module library; exclude it from Atlantis execution.
The App may still have read access there for cloning private modules. A deployment repository contains root configurations
referencing pinned modules, a tested provider lock file, a non-secret Azure Blob
backend configuration and explicit Atlantis projects. It must not depend on
ignored files from an operator's laptop or hardcode CLI-only authentication.

For example, in a separate deployment repository:

```yaml
# atlantis.yaml at the deployment repository root
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

The root's backend uses `use_azuread_auth = true`; storage account/container and
state key must be available to the runner. No new storage account is required
merely to run Atlantis. State permissions must not accidentally include management
state. Private modules also require App installation/read access to their repo;
that does not require adding their module-library repo to the execution allowlist.

Do not add `gitops/bootstrap` as an Atlantis project or hand this server the
management backend credentials. That root creates the hosting cluster, initial
Argo release and this identity; it also has CLI-dependent lifecycle steps. Keep
an independent operator/runner path for bootstrap, teardown and recovery.

## Human-in-the-loop workflow

1. Backstage scaffolds an organisation-owned repository with catalog metadata,
   an explicit `atlantis.yaml`, tested module references and deployment config.
   An engineer opens the infrastructure PR; existing repositories can do so directly.
2. A trusted team member comments `atlantis plan -p service-dev`.
3. Review the generated plan, tests and policy evidence, then approve the PR.
4. A trusted team member comments `atlantis apply -p service-dev`.
5. Merge after a successful apply, then check service readiness through Argo.

Autoplan and fork/draft PR handling are disabled. Applies require approval,
mergeability and an undiverged base; repository custom workflows and overrides
of those requirements are disabled. Bulk apply without a selected project is
disabled. PR locks complement the backend's state lease; the apply uses the saved
plan. Atlantis applies before merge. Reverting Git does not undo an apply: review
and execute a new plan to change infrastructure back.

Configure branch protection/rulesets separately: require relevant CI and owner
reviews, dismiss stale approvals on new commits and prevent bypass/self-approval
according to the organisation's model. Do not make the Atlantis apply status a
required pre-apply check, which would create a mergeability dependency cycle.
Re-plan and review again after base/code changes. These human controls are not
created by this Helm package and need live verification.

Backstage integration is an intended consumer of this workflow; the separate
Backstage branch is not changed by this package. Templates can create repositories,
generate the explicit projects and show links/status. New organisation repositories
are accepted without editing Atlantis's allowlist. The current workflow still
requires trusted-team plan/apply comments; automatic Backstage task execution is
not implemented here. Backstage does not need the managed identity credentials.

## Azure Service Operator integration boundary

The ASO package has merged, with only ResourceGroup CRDs enabled and no Azure
provisioning credentials configured. Atlantis remains independent of it. Once ASO
is deployed with the relevant CRDs and scoped permissions, a
package can declare supported Azure resources (such as its managed identity,
federated credential or storage) as ASO custom resources reconciled by Argo/ASO.
Use the existing platform Key Vault unless a separate vault is actually needed.
ASO cannot obtain a GitHub App private key from Azure: supply the external GitHub
credentials to Key Vault, then ESO reads them for Atlantis.

This PR keeps the Atlantis identity under Terraform's existing module loop.
Moving it to ASO is a later ownership change: do not have Terraform and ASO manage
the same Azure identity or role assignment simultaneously. Platform bootstrap must
supply ASO's initial authority; a package cannot grant itself Azure permissions.
For developer self-service, Backstage may generate either ASO resource manifests
for Argo or Terraform projects for Atlantis, depending on which engine owns that
resource. [ASO authentication and credential scopes](https://azure.github.io/azure-service-operator/guide/authentication/)
describe package/namespace-scoped identities and the bootstrap requirement.

## Operations and validation

Before deployment, define whether GitHub-hosted plans, comments, logs and metadata
are allowed by the company boundary. Self-hosting Atlantis does not make GitHub
an internal data store. The NetworkPolicy allows gateway ingress, DNS and HTTPS
egress; it is not an FQDN allowlist or an enforced data-residency perimeter.

`make validate` tests the package enabled/disabled, management/workload isolation,
TLS routing, secret references, disk, native auth settings and human apply gates,
and renders the pinned upstream chart twice to check stable Secrets. These are
offline checks. No GitHub App, Key Vault secrets, Azure roles or deployment are
created by rendering, and no live self-service success is claimed.

Before enabling for real workloads, exercise:

- Rejected commands from a user outside the team; rejected fork PRs.
- An unapproved apply, a stale review and a conflicting second PR.
- A harmless approved create/update, followed by a separately approved delete.
- Restart between plan/apply, secret rotation and an expired/denied Azure token.
- Failed/partial apply and a reviewed recovery plan; backend lease recovery.

Treat the PVC as sensitive: plans and checkouts may contain resource data. Disk
loss requires fresh plans and reconciliation of PR locks; it does not remove
remote Terraform state. Drain the service before an upgrade or `make down`:
pause webhook deliveries, prevent new commands and wait for all active Terraform
processes to finish. The Pod preStop hook waits for Terraform/Tofu processes for
up to the configured 300-second termination grace period; it does not guarantee
completion of longer runs or protect against forced termination. Application
teardown deletes the PVC but does not destroy externally managed developer
infrastructure; retire those roots through their own reviewed workflow first.
