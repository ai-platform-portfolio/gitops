# Backstage portal

The management cluster hosts Backstage at `https://backstage.<DNS_ZONE>` with
native Microsoft Entra sign-in. It reuses the existing CNPG PostgreSQL instance,
ESO, Envoy Gateway and cert-manager. There is one non-root application replica,
not another database server or registry.

## Application image and registry

The image contains this repository's frontend, backend, native Microsoft provider
and bundled TechDocs. GitOps pins the promoted image by digest. The initial image
is published to the existing `localacrk8s` Azure Container Registry; Terraform's
`container_registries` map grants its existing AKS kubelet identity `AcrPull` at
registry scope. No registry password, application Azure identity or pull Secret
is required. Application code and image build instructions are in
[backstage/](../../../backstage/README.md). Build and test the reviewed source
before publishing and promoting another immutable digest.

## GitOps ownership and ordering

| Stage | Wave | Owner |
| --- | --- | --- |
| Namespace | -10 | namespaces Application |
| Scoped reload controller | 5 | backstage-reloader Application |
| Entra identity and assignments | 7 | service-identities / Crossplane |
| Catalog configuration and generated credentials | 10 | configuration / ESO |
| PostgreSQL role and database | 30 | postgres / CloudNativePG |
| Database password and verified CA delivery | 40 | backstage-credentials / ESO |
| Portal | 60 | backstage Application |
| HTTPS route and per-host certificate | 80 | routes / Envoy and cert-manager |

Related stages share the existing Backstage template. The application has no
Kubernetes API token or cluster RBAC. A separate named-secret reader allows ESO
to deliver only the CNPG role password and CA. Generated role/session credentials
use immutable, orphaned CreatedOnce targets; do not delete them beside existing
data. The database uses its existing restricted role and verified TLS. Database auto-creation checks are disabled while plugin schema creation remains
enabled, so the app never needs access to the `postgres` maintenance database.
Its schema migrations bypass transaction pooling, with bounded per-plugin connection pools.

## Native Entra authentication

`identity.yaml` reuses the shared Crossplane `OIDCApplication` API. It creates a
single-tenant registration and credential, requires enterprise-app assignments,
and declares delegated Microsoft Graph `User.Read` for the profile lookup. The
callback is `https://backstage.<DNS_ZONE>/api/auth/microsoft/handler/frame`.
Declaring a delegated scope is not admin consent; tenant consent policy still
applies during interactive authentication.

`backstage.users` is a map of explicit Entra object IDs, catalog display names and
Backstage memberships. The same entries create the app assignments and catalog
Users. The native `userIdMatchingUserEntityAnnotation` resolver matches the Graph
profile ID to the catalog's `graph.microsoft.com/user-id` annotation. It needs no
email attribute, email fallback, guest provider or catalog bypass. Unknown catalog
users are rejected. `memberOf: [platform-team]` grants the existing operator policy;
an empty list retains reader permissions. These are Backstage permissions, not
Azure roles or automatic Entra group synchronisation.

The generated `backstage-sso` Secret supplies client ID, secret and tenant ID.
Catalog metadata and the initially empty integration configuration are GitOps
ConfigMaps. There are no manual Backstage Key Vault prerequisites. A scoped
Reloader restarts the deployment for SSO/database Secret or catalog/integration
ConfigMap changes; Argo preserves only its exact restart-marker annotation.
Credentials last 180 days by the explicit values setting. Scheduled rotation is
not configured; use a controlled credential replacement before expiry.

## Networking and exposure

The default-deny policy permits Gateway ingress, cluster DNS, the existing CNPG
pods and HTTPS to Microsoft's published AzureActiveDirectory service-tag IPv4
ranges. `entra-prefixes.yaml` records the retrieved ranges and date. Refresh this
data when Microsoft updates the tag; the authentication hosts can resolve to
different addresses over time. The range union is preserved exactly. This is IP
filtering, not FQDN enforcement or a private company perimeter. Additional approved
integration destinations belong in the explicit `backstage.egress` list; blanket
internet egress remains rejected by the values schema.

The current portal uses a public HTTPS Gateway. Do not add private company data,
external analytics or AI endpoints without the separately required company
boundary assessment. Empty integration settings do not enable Git publishing or
cloud provisioning; those credentials and permissions require separate setup.

## Acceptance and teardown

Validate image architecture/pull access, CNPG reconciliation, verified TLS startup,
readiness, catalog ingestion and Microsoft redirect before calling the portal
available. Complete an assigned-user browser login, check operator/reader
permissions, and verify an unassigned user is rejected before claiming end-to-end
SSO acceptance. A healthy pod or redirect alone does not prove sign-in.

Reverse Application waves remove the portal before its dependencies. Database
contents and generated application identities are disposable on full teardown;
there is no cross-teardown recovery mechanism. The existing Terraform state
backend and existing registry remain persistent.

Sources: [Microsoft provider and object-ID resolver](https://backstage.io/docs/auth/microsoft/provider/),
[Backstage sign-in resolvers](https://backstage.io/docs/auth/identity-resolver/),
[AKS registry access](https://learn.microsoft.com/en-us/azure/aks/cluster-container-registry-integration),
[Azure service tags](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview).
