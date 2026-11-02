# Shared management-cluster events

`events.enabled` installs the namespaced Argo Events controller and persistent
`platform` JetStream bus once on management. `events.sources` and `events.sensors`
are maps of **native Argo EventSource/Sensor specifications**, keyed by resource
name. Onboarding uses these same Helm definitions. Other platform orchestration
can consume them without copying a receiver or installing another controller.

The controller Application installs its CRDs at wave 0; event resources follow at
wave 30. Shared EventSource/Sensor templates use `SkipDryRunOnMissingResource=true`
because Argo validates resources before executing waves, and the child-installed
CRDs are absent on first install. This avoids blocking the controller installation;
normal dry-run validation still runs when the CRDs exist. See
[Argo's missing-resource sync option](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#skip-dry-run-for-new-custom-resources-types).

The current installation namespace is `cluster-onboarding`. Shared defaults supply
that namespace, the bus, node placement and small resource requests. The default
source identity can read/watch ConfigMaps there; the default Sensor identity can
get/create/update Jobs there. Other resources or namespaces require an explicitly
scoped ServiceAccount/RBAC configuration through the native template fields.
These shared platform inputs do not register application deployments in GitOps.

For example, a receiver for a producer that already supports rotating tokens:

```yaml
events:
  sources:
    catalog:
      service:
        ports:
          - port: 12000
            targetPort: 12000
      webhook:
        changed:
          port: "12000"
          endpoint: /changed
          method: POST
          authSecret:
            name: externally-rotated-webhook
            key: token

  sensors:
    catalog:
      dependencies:
        - name: change
          eventSourceName: catalog
          eventName: changed
      triggers:
        - template:
            name: refresh
            http:
              url: https://catalog.example.test/refresh
              method: POST
```

No receiver is enabled by this example's presence in documentation. Webhook
sources must specify `authSecret`; rendering an anonymous receiver fails. The
chart does not create that Secret or a permanent bearer token. Native `authSecret`
is literal token comparison, not OIDC verification. The producer/credential owner
must provide and rotate it; a long-lived token requires its own documented
implementation limitation. This bus exception does not authorize such tokens.
Server TLS, service exposure and backend authentication must also be configured
for an actual producer; no public route is supplied by this template.

For producers inside Kubernetes, prefer the onboarding pattern: patch a scoped,
pre-created signal with a projected service-account token and watch it using a
Resource EventSource. It requires no webhook password, receiver service or new
client-certificate machinery. The native source/Sensor maps still apply.

## GitHub preview delivery — optional shared package

`events.github.enabled` defaults to false. When enabled on management, one native
GitHub EventSource manages an organization webhook for PR and workflow-run events.
The existing App needs **organization Webhooks write** accepted on its installation;
repository Webhooks write is insufficient. The organization and existing signing
secret key are platform settings, not per-app registrations.

ESO mints a separate one-hour installation token with `organization_hooks: write`
and `actions: read`, refreshing every 15 minutes. The installed Reflector mirrors
only this token from `argocd` to the exact `cluster-onboarding` namespace. The App
key remains in `argocd`, and Argo's PR/Actions read-only token is unchanged. The
receiver projects the existing Key Vault `cluster-github-webhook-secret` for native
GitHub HMAC validation; GitHub webhooks do not supply OIDC identity. This reuses an
existing long-lived signing secret, not a new key. Automatic HMAC rotation is not
implemented; changing it requires coordinated webhook/receiver configuration.

The native GitHub client caches the registration token at startup. Its bounded hook
registration retries run during the first ten minutes, inside the token's lifetime;
normal incoming events use HMAC rather than the API token. Source restarts read the
current mirrored token. `deleteHookOnFinish: false` intentionally retains the one
organization subscription across disposable pod/cluster restarts, avoiding expired
shutdown credentials and rolling-delete races. Permanent platform retirement must
remove that retained organization webhook; individual preview cleanup does not.

The shared Sensor accepts PR open/reopen/push/close and completed workflows. Fixed
native kubectl Jobs refresh preview-labelled ApplicationSets, then Applications.
Only `platform.ai-platform-portfolio/preview=true` resources are selected; GitHub
payloads never supply object names, shell commands or arguments. Jobs have a
60-second deadline, two retries and five-minute TTL, and reuse the pinned kubectl
image. A dedicated projected-token ServiceAccount gets get/list/patch on Argo Apps
and AppSets in `argocd`. Kubernetes RBAC cannot restrict patches to annotations or
labels, so this is a trusted platform workload; application pods get none of it.
Refreshes cover all previews on a relevant organization event. This is proportionate
for the portfolio and keeps routing declarative; it is not a per-repository queue.
The existing source/Sensor controller and JetStream bus are reused.

At startup, a native resource watch emits the existing `preview-refresh` signal
ConfigMap (`afterStart: false`) for full catch-up. Its `/data` is producer-owned:
patch it to request a repeatable catch-up after a missed delivery. GitHub does not
automatically redeliver failed webhooks; native delivery replay is another recovery
option. There is no PR polling or timer that discovers missed GitHub events. Failed
refresh Jobs require retry/replay if their bounded retries expire.

TLS uses `github-events.<DNS_ZONE>/github`, with an annotated ListenerSet and
cert-manager-generated certificate. No explicit Certificate, extra Gateway or
anonymous receiver is added. The existing App's Atlantis endpoint is unchanged.
Before activation, verify installation permissions, mirrored-token freshness,
certificate/route readiness, actual hook registration, rejected unsigned/invalid
signatures, PR/workflow events, startup catch-up and duplicate/replay behavior.
Render/dry-run checks do not prove those live gates. Chart-generated preview labels
must be installed first; enabling the package alone registers no applications.

Sources: [native GitHub source](https://github.com/argoproj/argo-events/tree/v1.9.11/pkg/eventsources/sources/github),
[organization webhooks](https://docs.github.com/en/rest/orgs/webhooks),
[Reflector namespace restrictions](https://github.com/emberstack/kubernetes-reflector#usage).

## Long-lived credential exception: managed JetStream

**AI-119 exception, approved by the user:** Argo Events 1.9.11's managed JetStream
installer generates and reuses server/client authentication passwords and TLS
private keys/certificates. Certificates are generated with ten-year validity.
The native bus integration does not support Kubernetes projected-token or Entra
OIDC authentication. We retain the native integration rather than add a custom
broker/authentication service for this portfolio.

Scope: only the Argo-managed internal bus Secrets in `cluster-onboarding`.
Atlantis and application pods receive none of these credentials. The Argo
controller owns them; no credential material appears in Git or Helm values.
They persist with the bus; **automatic credential rotation is not implemented**.
Do not delete its live Secrets as a rotation procedure: the server Secret also
contains the encryption key for retained events. Reassess this exception when
upgrading Argo Events or introducing a supported rotating bus integration.
The same exception is noted beside `eventbus.yaml`.

The bus has one replica and a 1 GiB PVC; retained events are bounded to seven days
and 100 MB. Pod restarts retain queued work; disposable cluster teardown does not.
This is not HA. The change adds/restores no NetworkPolicies.

Sources: [native JetStream installer](https://github.com/argoproj/argo-events/blob/v1.9.11/pkg/reconciler/eventbus/installer/jetstream.go),
[resource events](https://argoproj.github.io/argo-events/eventsources/setup/resource/),
[projected service-account tokens](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/).
