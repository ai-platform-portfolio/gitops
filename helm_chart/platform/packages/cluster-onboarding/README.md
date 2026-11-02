# Event-driven workload onboarding

Management receives Atlantis's native HTTP `apply` notifications through a private
Argo Events webhook. A chart-generated Sensor for each reviewed cluster profile
accepts only successful applies from `clusterOnboarding.repository`, workspace
`default`, and directory `clusters/<cluster.name>`. The cluster name and immutable
worker image come from GitOps, never from webhook parameters. Failed applies and
unknown directories cannot launch an onboarding worker.

```text
Atlantis apply -> authenticated EventSource -> persistent JetStream EventBus
  -> matching cluster Sensor -> onboarding Job -> workload Argo CD/platform
```

The existing cluster profile is unchanged:

```yaml
cluster:
  name: aks-example
  role: workload
  location: uksouth
  resourceGroup: rg-aks-example

request:
  issue: AI-123
```

## Ownership and operation

GitOps owns the shared Argo Events installation, generated token, event bus,
Sensors and Job templates. Terraform still owns AKS; the worker
still uses its dedicated workload identity and existing operator/Argo resources.
No Terraform state, backend credentials or new per-cluster webhook are required.
The package is installed only on management when cluster onboarding is enabled.

ESO generates an immutable token once and Reflector copies it only to Atlantis.
Atlantis reads the JSON Authorization header from the reflected Secret, while the
EventSource mounts the token. Neither value enters Git or Helm values. The
ClusterIP endpoint has no public route and requires the generated bearer token.
This change adds no NetworkPolicies; the platform policies were removed under
AI-91 and the existing Argo exclusion remains. Native bus credentials and a
1 GiB PVC hold accepted events for
up to seven days (100 MB stream limit). This is one JetStream replica, not HA.
Normal pod restarts retain queued messages; cluster teardown does not.

The Sensor uses native Kubernetes `update`, which creates an absent Job and
updates an existing Job without starting another execution. One stable Job name
per cluster coalesces signals while that cluster's onboarding is active; different
clusters have separate Jobs. The worker reads current reviewed GitOps intent,
not deployment configuration from the event. Delivery is not exactly-once: a late
duplicate can start another idempotent run after cleanup.

Job retries are bounded to two replacement attempts, with an eight-hour overall
deadline. Sensor dispatch retries use five attempts with exponential backoff.
Finished Jobs are immediately eligible for Kubernetes TTL cleanup, releasing the
name for the next apply; consult shared logs and `workload-status-<cluster>` in
`argocd` for results. While a Job is active, changing its immutable pod template
will fail closed; let it finish before rolling out a different worker template.

The worker requests 50m CPU and 256 MiB memory, with no CPU limit and a 768 MiB
memory limit. For one cluster, controller/bus/source/Sensor request another 60m
CPU and 320 MiB memory, plus bus sidecars. These are initial small-portfolio
settings, not measured peak sizing. The persistent bus also needs one available
Azure disk attachment; check placement before deployment. No node increase or
system-pool toleration is introduced.

## Rollout, retry and replay

Deploy the controller/CRDs before the event resources, and the generated Secret
before Atlantis. The chart's Application waves order resource installation; verify the generated
EventSource/Sensor pods and EventBus are Ready before testing an apply. Before
initial replay, confirm the old CronJob and its owned Jobs have been pruned so
an old worker cannot overlap the new Job. Do not replay before the cluster profile
has reconciled and the matching Sensor exists.

Atlantis 0.48.2 sends an HTTP notification once; it does **not** retry a failed
HTTP delivery. Event-bus retention starts only after receipt, and a successful
webhook response confirms receipt, not onboarding success. A sender outage,
expired event, event received before its Sensor exists or exhausted dispatch/Job
retries therefore needs explicit operator replay. There is no timer fallback.

After reviewing the current profile and actual cluster state, an operator can
replay the chart-owned Job without reconstructing a manifest or handling tokens:

```sh
cluster=aks-example
kubectl -n cluster-onboarding get sensor "onboard-$cluster" \
  -o jsonpath='{.spec.triggers[0].template.k8s.source.resource}' |
  kubectl create -f -
```

`AlreadyExists` means the stable Job name is still present: inspect its state;
do not delete an active Job merely to replay. Failed Jobs become eligible for TTL
cleanup too. If intent changes during an active run, replay after it finishes to
ensure that run observes the latest intent. Removing a profile removes its Sensor;
check for an active Job before deleting the corresponding cluster.

The ten-minute CronJob is removed. The existing worker's finite readiness retries
remain unchanged: this PR replaces scheduled onboarding discovery, not every
readiness check inside a running onboarding operation. Kubernetes watches for
those gates would require a separately built and promoted worker image.

## Evidence and sources

The repository validator checks event filters, Job identity/resources, ordering,
management-only installation, and the actual upstream Atlantis environment render.
Local native acceptance uses the pinned controller, persistent bus and rendered
Sensor with a harmless worker; this is not an Azure workload-cluster deployment.
Deployment acceptance must still exercise an actual Atlantis apply, workload
identity and onboarding, pod restart/queued delivery and the existing platform's
capacity/disk headroom.

- [Atlantis apply notifications](https://www.runatlantis.io/docs/sending-notifications-via-webhooks)
- [Installed Atlantis sender: one HTTP attempt](https://github.com/runatlantis/atlantis/blob/v0.48.2/server/events/webhooks/http.go)
- [Argo Events webhook authentication](https://argoproj.github.io/argo-events/eventsources/webhook-authentication/)
- [Native create/update trigger semantics](https://github.com/argoproj/argo-events/blob/v1.9.11/pkg/sensors/triggers/standard-k8s/standard-k8s.go)
