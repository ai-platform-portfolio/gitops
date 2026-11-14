# Application telemetry

[The Application](../../templates/opentelemetry.yaml) installs the OpenTelemetry
Collector at wave 41, after LGTM backends. `values.yaml` forwards traces to Tempo,
logs to Loki and metrics to Mimir. The Argo Application/release name remains
`opentelemetry-collector`; its namespace is `opentelemetry`.

Application instrumentation sends OTLP to this collector. Kubernetes metrics and
pod logs are configured separately in `../k8s-monitoring/`.

## Private Foundry trace ingestion

When `gateway.private.enabled` is set, the package adds an HTTPRoute for POST
`https://otel.internal.<runtime.DNS_ZONE>/v1/traces`, forwarded to the existing
collector receiver on 4318. The Gateway owns TLS through cert-manager automation;
there is no duplicate Certificate or externally exposed collector Service.
Existing in-cluster OTLP clients keep their current endpoints.

The Foundry runtime uses its existing delegated subnet and VNet peering to reach
the AKS internal load balancer. The receiver relies on HTTPS and the Gateway's
source-subnet restriction. The agent supplies no Langfuse key. Resource attribute
`langfuse.export="true"` forwards all its spans to Langfuse as well as Tempo;
unmarked spans continue to Tempo only. Collector-side credentials stay unchanged.

Set the consuming repository's optional `AGENT_TRACES_ENDPOINT` variable to the
HTTPS trace URL after verifying this private path. Leave it unset to disable
export. This is a shared-package dependency, not an agent deployment prerequisite:
bounded background exporters may drop spans during outages without blocking
inference. GitHub-hosted evaluation runners cannot reach this private receiver;
they retain evaluation reports and the hosted trace IDs instead.
