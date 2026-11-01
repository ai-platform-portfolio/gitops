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
