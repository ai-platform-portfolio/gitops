# Certificates

[The Application](../../templates/cert-manager.yaml) installs cert-manager after
Envoy supplies Gateway API CRDs. `values.yaml` enables Gateway API/ListenerSet
reconciliation and workload identity.

`issuer.yaml` configures the ACME issuer and Azure DNS-01 solver. It is applied
by the shared `configuration` Application. Individual application ListenerSets
carry issuer annotations; cert-manager creates a certificate for each hostname.
Networking files live with LGTM, Langfuse and SonarQube.

Runtime inputs supply the ACME email, DNS zone and Azure identity/context. No
wildcard certificate or separate duplicate Certificate manifest is needed.
