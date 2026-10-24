# Logs, dashboards, traces and metrics

[The Applications](../../templates/lgtm.yaml) install Loki, Grafana, Tempo and
Mimir independently at wave 30. Their chart pins are explicit there;
`values.yaml` groups only these four related releases under their release names.

| File | Owning Application | Purpose |
| --- | --- | --- |
| `service-account.yaml` | configuration | Shared Azure workload identity for object storage |
| `grafana-secret.yaml` | configuration | Grafana Entra credentials from Key Vault through ESO |
| `networking.yaml` | routes | Grafana ListenerSet, HTTPS route and HTTP redirect |
| `mimir-eviction.yaml` | mimir, final source | Override only the eviction webhook failure policy |
| `namespace.yaml` | namespaces | Namespace and public-gateway attachment label |

The Mimir override intentionally produces an Argo `RepeatedResourceWarning`.
Other webhooks keep their upstream failure policy. Credentials are prepared before
the root is seeded; Azure storage references arrive through runtime values.

Object storage containers are Terraform-managed and disposable. Grafana and
working data use PVCs. This is a small portfolio configuration, not a validated
production capacity profile. Kubernetes collection lives in `../k8s-monitoring/`;
application OTLP collection lives in `../opentelemetry/`.

AKS adds its control-plane namespace exclusions to the rollout-operator webhooks.
The shared Application diff rule normalizes only that exact expression array;
the existing `lgtm` namespace label selector and webhook failure policies remain
under GitOps comparison.
