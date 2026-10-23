# Shared gateway

[The controller Application](../../templates/envoy-gateway.yaml) installs Envoy
Gateway and its Gateway API CRDs. Cert-manager starts after those CRDs exist.

`gateway.yaml` defines the GatewayClass and shared HTTP Gateway. The separate
[gateway Application](../../templates/gateway.yaml) applies these resources after
controller/configuration readiness. Namespaces labelled
`gitops.platform/gateway: public` may attach application-owned ListenerSets.

HTTPS listeners, per-host TLS Secret references and routes live in each exposed
application's `networking.yaml`. The `routes` Application attaches them after
backend health; cert-manager issues the certificates from ListenerSet annotations.
