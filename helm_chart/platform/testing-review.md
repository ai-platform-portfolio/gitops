# Testing and progressive delivery review

Reviewed against GitOps main `e44f873` on 2026-10-25. The authorized implementation
is free standalone Testkube plus Argo Rollouts, delivered through the existing
`make up` lifecycle. AKS is currently torn down; source configuration and local
integration do not prove a live Azure deployment.

| Capability | Implementation |
| --- | --- |
| GitOps reconciliation | Existing Argo CD, native Entra SSO/RBAC and current-source child health gates |
| Progressive delivery | Rollouts `v1.10.1`, two leader-elected replicas, Gateway API plugin `v0.17.0`, scoped route RBAC, metrics and teardown guard |
| Test execution | Central standalone Testkube `2.14.2`, GitOps workflow definitions and restricted/tokenless test defaults |
| Database | Existing shared CNPG instance, dedicated nonsuperuser role/database, verified TLS, ESO credential delivery and explicit schema gate |
| Broker | Official NATS chart `2.15.1`, mTLS and 1Gi persistent JetStream |
| Artifact/log storage | Stateless S3Proxy `4.1.1` to existing Azure Blob account through workload identity; no local object-store disks |
| Azure dependencies | ASO-managed storage identity, federation, account-scoped role, two new containers and one canonical lifecycle policy |
| Entra management | Existing Crossplane foundation retained; no duplicate Entra application owner or artificial standalone SSO session |
| Release gate | Blocking CLI workflow analysis with finite deadline; application-specific test/route adoption remains application owned |
| Workflows / Events | Not installed; neither is required by Testkube |

The prerequisite analysis found ASO had no usable initial provisioner credential.
A generic bootstrap identity/federation/scoped-role entry now supplies that initial
controller authority through the existing Terraform module loop. This avoids a
manual Key Vault secret or separate bootstrap handoff; all Testkube service
resources and configuration stay in GitOps.

One-command sequencing gates Azure resources, generated credentials/certificates,
CNPG, broker, adapter, API, schema and actual artifact/failure checks before exposing
the release analysis template. S3 compatibility requires an adapter: Testkube does
not natively speak Azure Blob, while the selected adapter uses Azure's native SDK.
Azure lifecycle supplies retention because S3Proxy's S3 lifecycle API is unsupported.

Standalone's enterprise limits are explicit: no native Entra application RBAC,
public dashboard or enterprise audit guarantee. Private AKS/Kubernetes-authorized
CLI access and network-scoped machine access are the supported initial paths.
The pinned workflow-init client skips TLS certificate verification for presigned
artifact URLs upstream; do not claim every storage client verifies the CA.
Management-local gates are provided; workload clusters get Rollouts and require
a reviewed cross-cluster API/authentication design before using central Testkube.

Application rollout adoption, real business assertions/SLO metrics and failure
acceptance remain necessary per application. The platform installation checks are
not a substitute. PostgreSQL permission ownership is documented for a later Atlas
transfer without taking over Goose migrations implicitly.

See the [Testkube implementation guide](packages/testkube/README.md) for the full
wave table, ownership, authentication limitations, validation and permission note;
see [Rollouts](packages/argo-rollouts/README.md) for application traffic ownership.
