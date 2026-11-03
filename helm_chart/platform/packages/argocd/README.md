# Argo registry access

Argo reuses its existing bootstrap-managed identity. The bootstrap caller adds
`repoServer` to `argocd.identity_components`, a matching service-account federation,
and registry-scoped `AcrPull` through the existing identity module. Registry scopes
are available as `registry_<key>` from the configured `container_registries` map.
No identity is recreated or transferred between Terraform and ASO.

The existing Helm wiring supplies the identity annotation and Workload Identity
pod label. No exported client-ID ConfigMap, Reloader, token-generation script or
manual client-ID handoff is required. The existing server/controller share the
read-only registry permission as components of the same Argo installation.

GitOps owns only the native Helm OCI prefix credential configuration. Set
`argocd.registry.name` in platform values; application repositories need no registry
credential entries. The Secret contains metadata flags, not a registry password.
The current ACR uses legacy registry permissions; ABAC-enabled registries need the
corresponding reader role. Existing identities/federations retain single ownership.

Review/apply the saved bootstrap plan through the existing Terraform owner after
merge. It should add only the repo-server federation, the read-only registry role
and update the Argo Helm release. Verify the running repo-server and a real private
chart pull before claiming integration works; render checks do not prove auth.
For rollback, remove these added bootstrap entries and the prefix credential after
all dependent Applications/deletion hooks finish. ApplicationSet and deployed
Testkube checks remain separate work.

## Shared GitHub API access

The `argocd-github` package reuses the existing `ai-platform-clusters` GitHub App
and its all-repository installation. `argocd.github` platform values supply the
existing App/installation IDs and Key Vault key name; no private key or token is
stored in Helm values or Git. No new App, PAT, Azure identity or per-app list is
created. This is service configuration, not a bootstrap/Terraform handoff.

`github.yaml` contains three native ESO resources, ordered within the Application:

1. An ExternalSecret imports only the existing private key from `azure-kv` into
   `Secret/argocd-github-app` (wave -10).
2. A GithubAccessToken generator references that key and requests
   `actions: read`, `pull_requests: read` and `statuses: write` (wave -5). GitHub also supplies its
   implicit metadata-read permission. Repository access follows the existing App
   installation, without copying its broader write permissions into the token.
3. An ExternalSecret materializes `Secret/argocd-github-api` with key `token`,
   refreshed every 15 minutes, ahead of GitHub's one-hour token expiry (wave 0).

All resources and both resulting Secrets stay in `argocd`. Consumers receive the
scoped installation token, not the App private key. There is no reflection or workload mount
configured here. Future PR generators can use `tokenRef` with Secret
`argocd-github-api`, key `token`; publication-success gates can use the same API
credential. Those application templates and their bindings remain separate work.
Consumers must reread rotated credentials rather than caching them indefinitely.

Deployed browser status hooks mount this token in short lived Jobs in `argocd`.
The App installation already grants status write. No preview namespace receives
the token; this shared credential now has commit-status write permission.

The installed/pinned ESO API accepts `appID` and `installID` strings; it does not
support the ID Secret-reference fields shown in newer documentation. Reuse the
native schema rather than adding a token-generation script or controller.
[ESO GitHub generator](https://external-secrets.io/latest/api/generator/github/).

Merging allows normal GitOps reconciliation of this shared credential package.
Before claiming live readiness, verify both ExternalSecrets Ready, required API
read calls, permission restrictions and renewal across expiry without logging or
persisting token values. Render/schema checks do not prove the private key or App
installation access works. This PR does not register applications or deploy previews.
For rollback, remove consumers before pruning the package and its owned Secrets.
