# DNS records

[The Application](../../templates/external-dns.yaml) installs ExternalDNS after
its Azure configuration is available. `values.yaml` defines its sources, identity,
DNS ownership settings and ListenerSet discovery/RBAC. `azure-config.yaml` is
applied earlier by the `configuration` Application.

ExternalDNS discovers the application-owned routes and ListenerSets attached to
the shared Gateway. Public networking lives with LGTM, Langfuse and SonarQube.
The lifecycle removes routes before ExternalDNS during teardown and allows one
record cleanup interval before continuing.
