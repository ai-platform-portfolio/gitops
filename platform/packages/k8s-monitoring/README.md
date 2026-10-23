# Kubernetes telemetry

[The Application](../../templates/k8s-monitoring.yaml) installs Grafana Kubernetes
Monitoring at wave 40. `values.yaml` enables Kubernetes metrics and pod logs,
configures the telemetry collector and forwards data to Mimir and Loki.

The cluster name comes from runtime values. Its `monitoring` namespace is created
in the shared namespace stage. This package is independently pinned and configured
from the four LGTM backend releases; application OTLP collection is separate in
`../opentelemetry/`.
