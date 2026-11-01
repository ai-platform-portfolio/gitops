# Argo registry reader

This shared package lets Argo read private Helm OCI releases without registry
passwords or application-specific credential entries. `argocd.registry` in platform
values selects the existing registry name/resource group and identity location.
It currently uses `AcrPull` for the portfolio's legacy registry permission mode;
an ABAC-enabled registry needs its corresponding repository-reader role instead.

ASO creates a dedicated cluster-named identity, federation for the existing
`argocd-repo-server` service account, and one registry-scoped `AcrPull` assignment.
It exports the client/principal IDs to `argocd-registry-identity`. A prefix credential
Secret enables native Helm OCI Workload Identity for repositories under that ACR.
No client secret, admin password or per-app registry list is needed.

The existing bootstrap Helm release still owns repo-server and its service account.
Its values opt the pod into Workload Identity and read `AZURE_CLIENT_ID` from ASO's
ConfigMap. Admission preserves that environment entry and injects the projected
federated token/tenant settings. No resource ownership is transferred to GitOps.

On a fresh cluster, the ConfigMap does not exist until Argo installs ASO and this
package. Its reference is therefore optional: repo-server can initially read public
Git. The namespace-scoped Reloader starts at root wave5, ASO at wave10, and this
package at wave20. `reloadOnCreate` rolls repo-server when ASO creates the ConfigMap;
later client-ID changes also roll it. Only the explicitly annotated deployment is
selected; Secrets and Jobs are ignored. This avoids a bootstrap dependency cycle
and manual client-ID handoffs. Reloader is already used by other platform packages.

For an existing cluster, let GitOps create the reader resources, then review/apply
the bootstrap Helm values update through its existing Terraform owner. Check the
ASO resources' current-generation readiness and repo-server rollout. Azure role
propagation may lag resource readiness. A real private release pull is required
before claiming registry integration works; render/admission checks alone do not
prove authentication. No new Terraform identity, module or runtime output is added.

The ASO controller already has bootstrap authority to create identities and assign
roles. Its watch list includes `argocd`; namespace credential metadata comes from
the existing runtime context. Application namespaces never receive that credential.

Rollback the repo-server settings through the bootstrap Helm owner before removing
the reader package. Removing registry access prevents new private pulls; retain it
and the release packages until dependent Applications and deletion hooks finish.
ApplicationSet lifecycle and deployed Testkube checks remain separate work.
