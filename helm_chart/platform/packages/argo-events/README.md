# Shared management-cluster events

`events.enabled` installs the namespaced Argo Events controller and persistent
`platform` JetStream bus once on management. `events.sources` and `events.sensors`
are maps of **native Argo EventSource/Sensor specifications**, keyed by resource
name. Onboarding uses these same Helm definitions. Other platform orchestration
can consume them without copying a receiver or installing another controller.

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
