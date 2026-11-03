# Kargo

The management cluster installs the official Kargo chart at version 1.12.5.
`templates/kargo.yaml` owns the Entra identity and chart Applications. The
namespace is created by the shared `namespaces` stage; the TLS ListenerSet and
HTTPRoutes join the shared `routes` stage after the API is healthy.

Kargo uses native OpenID Connect with a passwordless Entra SPA registration.
The `PublicOIDCApplication` Crossplane composition creates the registration and
enterprise principal, then publishes only its client ID in `Secret/kargo-sso`.
The API reads that ID from the Secret. `kargo.admins` lists Entra user object IDs;
each user receives an enterprise-app assignment and Kargo's administrator claim.
The built-in password account and Dex are disabled. This initial package supports
browser login; a CLI localhost redirect URI is not registered.

Kargo's API accepts HTTP only inside the cluster. The platform Gateway terminates
public HTTPS at `kargo.<DNS_ZONE>` and cert-manager provisions its per-host
certificate. Kargo's own admission webhook certificates stay chart-managed.

This package installs Kargo's control plane and CRDs. It does not create a
`Project`, `Warehouse`, `Stage` or promotion policy for an application. Those
are rendered by the reusable application chart. `kargo-acr` creates an
ASO-owned identity, Kubernetes federation and registry-scoped `AcrPull` role.
The controller receives its reflected client ID and a projected short-lived
service-account token; its pod does not use a retained Azure credential. App
Project Git credentials are exact-repository ExternalSecrets from the existing
GitHub App key in Key Vault. This does copy the App key into each Project
namespace, so Kubernetes access to those namespaces must stay restricted; the
GitHub App's Contents permission still needs live verification. Kargo's native
Warehouse discovery is periodic; an ACR webhook can reduce discovery latency
after the basic path is proven.

After a merge and an
authorized `make up`, check the Entra registration and assignment, the generated
client-ID Secret, the API and webhook health, the Gateway route, ACR identity
and browser SSO. No live delivery claim is made by a render.
