# Reflector

`templates/reflector.yaml` installs EmberStack Reflector/chart 10.0.65 at wave 0
on each cluster, after the shared namespaces Application creates its restricted
namespace. One controller replica watches Secrets and ConfigMaps for annotation
opt-in. It has no portal, external route, storage or SSO requirement; Kubernetes
RBAC and existing Argo CD SSO govern management.

This installation adds no reflection annotations or mirrors. Existing ESO,
CNPG and lifecycle credential delivery keep their current ownership. Reflector
requires cluster-wide Secret/ConfigMap read and write permissions; annotations
control copying behavior, not the controller's RBAC. Users who can annotate
sources can permit cross-namespace copying, so source write access is privileged.

For a later replication request, annotate the owning source template in GitOps
(for generated Secrets, use the generating controller's supported template):

```yaml
metadata:
  annotations:
    reflector.v1.k8s.emberstack.com/reflection-allowed: "true"
    reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces: "^destination-namespace$"
    reflector.v1.k8s.emberstack.com/reflection-auto-enabled: "true"
    reflector.v1.k8s.emberstack.com/reflection-auto-namespaces: "^destination-namespace$"
```

Replace the example with explicit approved destinations. Empty/omitted namespace
allowlists permit every namespace; avoid wildcard access. Do not add namespace
selectors alongside these allowlists assuming they narrow access: upstream
combines the two alternatives with OR. No replication of existing credentials is
authorized by this installation alone.

Automatic mirrors belong to Reflector: do not also manage their data through
Argo or ESO. Source deletion or removal of reflection permission deletes automatic
mirrors. Existing conflicting resources are skipped. Keep per-host certificate
issuance with the current application-owned ListenerSets; this does not introduce
wildcard certificates or migrate certificate ownership.

See the [upstream usage guide](https://github.com/emberstack/kubernetes-reflector).
