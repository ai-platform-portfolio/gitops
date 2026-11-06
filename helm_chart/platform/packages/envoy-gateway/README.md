# Shared gateway

[The controller Application](../../templates/envoy-gateway.yaml) installs Envoy
Gateway and its Gateway API CRDs. Cert-manager starts after those CRDs exist.

`gateway.yaml` defines the GatewayClass and shared Gateway. The separate
[gateway Application](../../templates/gateway.yaml) applies these resources after
controller/configuration readiness. The existing HTTP listener and application-owned
ListenerSets remain available to namespaces labelled `gitops.platform/gateway: public`.
Ordinary services keep per-host ListenerSets and cert-manager-issued certificates.

## Preview HTTPS

Platform values `gateway.preview` configure an optional shared listener, enabled
by default. Its hostname defaults to `*.previews.<runtime.DNS_ZONE>`; `hostname`
can override that wildcard, including templated platform values. Listener name,
certificate Secret name and ClusterIssuer are configurable. Set `enabled: false`
to retain an HTTP-only shared Gateway and remove its issuer annotation.

The existing Gateway receives the issuer annotation and an HTTPS listener with a
central Secret reference. Cert-manager creates and renews the wildcard Certificate
through the existing Azure DNS-01 issuer. There is no duplicate Certificate manifest,
new Azure identity or copied private key. Both Certificate and Secret live in
`envoy-gateway-system`. This does not create wildcard DNS records: ExternalDNS still
publishes exact HTTPRoute hostnames through the existing setup.

The HTTPS listener accepts routes only from namespaces with BOTH labels:

```yaml
gitops.platform/gateway: public
gitops.platform/preview: "true"
```

The application chart's `platform.gateway.sharedListener` selects the listener
(default package name `preview-https`); its Gateway name/namespace must match this
package. Applications retain exact hostnames under the wildcard, for example
`repo-pr-7.previews.example.test`. Their routes and namespaces are independent;
removing a preview does not remove the shared listener/certificate. ApplicationSet
wiring supplies these settings and labels centrally, without per-app access lists.
The onboarding template derives the preview domain from `gateway.preview.hostname`
by removing `*.`; changing this one value updates both the shared certificate
hostname and generated preview routes. Existing preview URLs change with it.
This package itself does not register or deploy applications.

Merging permits normal GitOps reconciliation of the shared listener/certificate.
Existing applications retain their current ListenerSets. Before using the new
listener, verify Gateway programming, Certificate Ready, route Accepted/ResolvedRefs,
DNS and verified HTTPS. Render checks alone do not prove issuance or connectivity.
Disabling the feature does not prove cert-manager has removed previously generated
TLS resources; confirm the observed certificate lifecycle when retiring the listener.
See [cert-manager Gateway issuance](https://cert-manager.io/docs/usage/gateway/).

The `previews` default replaces `preview` after repeated disposable rebuilds hit
Let’s Encrypt’s exact-identifier issuance limit. A new hostname set permits a new
order, subject to other CA limits; it does not solve certificate reuse across
future rebuilds. Certificates and private keys are still disposable with the cluster.
