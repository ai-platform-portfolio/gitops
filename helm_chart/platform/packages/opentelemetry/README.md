# Application telemetry

[The Application](../../templates/opentelemetry.yaml) installs the OpenTelemetry
Collector at wave 40, after LGTM backends. `values.yaml` forwards traces to Tempo,
logs to Loki and metrics to Mimir. The Argo Application/release name remains
`opentelemetry-collector`; its namespace is `opentelemetry`.

Application instrumentation sends OTLP to this collector. Kubernetes metrics and
pod logs are configured separately in `../k8s-monitoring/`.
