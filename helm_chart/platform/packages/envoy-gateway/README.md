# Shared gateway

[The controller Application](../../templates/envoy-gateway.yaml) installs Envoy
Gateway and its Gateway API CRDs. Cert-manager starts after those CRDs exist.

`gateway.yaml` defines the GatewayClass and shared Gateway. The separate
[gateway Application](../../templates/gateway.yaml) applies these resources after
controller/configuration readiness. The existing HTTP listener and application-owned
ListenerSets remain available to namespaces labelled `gitops.platform/gateway: public`.
Ordinary services keep per-host ListenerSets and cert-manager-issued certificates.

## Private shared-package ingress

`gateway.private` adds a separate private Gateway using the same Envoy controller.
Its EnvoyProxy configures an AKS internal load-balancer Service, with source ranges
restricted to the existing Foundry delegated subnet. The hostname defaults to
`otel.internal.<runtime.DNS_ZONE>`. Only HTTPS/443 is exposed; the public Gateway
and its listeners are unchanged. Disable `gateway.private.enabled` to remove this
private ingress and its OTel HTTPRoute. The existing internal collector Service
continues serving in-cluster clients.

The installed Gateway API requires a base listener. This Gateway's initial
HTTPS listener serves the shared collector and carries the existing cert-manager
issuer annotation. Cert-manager creates/renews its Certificate and Secret; no
handwritten Certificate is added. Further shared packages can own issuer-annotated
ListenerSets in namespaces labelled `gitops.platform/gateway: private`.
ExternalDNS publishes the HTTPRoute hostname to the Gateway's private address in
the existing public DNS zone; no new private DNS zone/link or VNet peering is needed.

This is TLS plus subnet-based network trust, not per-agent identity authentication.
Do not widen source ranges or publish this receiver through the public Gateway
without a separate security/network review. Verify internal Service address,
Gateway/route conditions, certificate issuance, DNS and actual Foundry export
after reconciliation. Server dry-run alone does not establish reachability.

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
