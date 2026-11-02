# Event-driven workload onboarding

```text
Successful Atlantis apply -> Kubernetes signal ConfigMap (HTTPS + projected JWT)
  -> Resource EventSource watch -> shared platform bus -> per-cluster Sensor/Job
```

The server-controlled `platform-onboarding` workflow keeps native Atlantis
`init`, `plan` and `apply` steps. Its publisher runs only after `apply` succeeds;
a failed step stops the workflow. Repository-defined workflows and overrides stay
disabled. The script is mounted read-only from GitOps, outside the checkout.
Cluster profiles remain unchanged; names, signals and routing derive from them.
No webhook bearer Secret, generator, reflected credential or receiver is created.

## Authentication and ownership

Atlantis explicitly projects an API-audience service-account JWT with a requested
600-second lifetime, plus the existing Kubernetes API CA. The kubelet handles token
renewal. The publisher reads the current token on each invocation and sends its
Authorization header through curl's stdin, not process arguments or PR output.
The existing Azure federation token has a separate audience and mount.

RBAC permits Atlantis only to **patch** the exact pre-created onboarding signal
ConfigMaps in `cluster-onboarding`. It cannot create/read/list signals, read Secrets
or create Jobs through this role. Its existing ASO readiness permissions remain.
The source watches ConfigMaps using its own service account; the Sensor separately
gets/creates/updates Jobs. The worker keeps its existing Entra workload identity.
The authenticated Atlantis pod is the producer trust boundary: code executed by
Atlantis shares that pod identity. This does not cryptographically attest that
Terraform succeeded; the server-owned workflow orders the normal execution path.

The publisher validates repository, default workspace, cluster directory and
source revision once. The signal contains only the revision and a fresh request
marker; its ConfigMap name identifies the cluster. Sensors match that name and
require both fields, so empty or unrelated signals do not release a worker.
Bash is already included in the pinned Atlantis image. The native Resource
EventSource watches ADD/UPDATE, with
`afterStart: false`, so an existing signal is read after watcher startup too.

Each signal holds the latest request. `platform-events` ignores only `/data` on
these exact named ConfigMaps, preserving producer-owned data through GitOps sync.
Routing labels and all other resources still reconcile normally. Native Job update
coalesces signals under one stable Job name per cluster; a duplicate during a run
preserves its UID. Late duplicates/restarts can rerun idempotently after cleanup.
This is current-intent convergence, not an execution history for every revision.
If intent changes during an active run, replay after completion when needed.

Dispatch has five bounded attempts; Jobs have two replacement attempts and an
eight-hour deadline. Finished Jobs are immediately eligible for TTL cleanup.
The worker requests 50m CPU/256 MiB memory, no CPU limit and 768 MiB memory limit.
Its finite readiness retries are unchanged. See the [shared event package](../argo-events/README.md)
for reusable native webhook/Sensor inputs and the **managed JetStream long-lived
credential exception**; that is the only new permanent credential exception here.

## Rollout and recovery

No Azure deployment is claimed by local tests or CI. Check pod requests and one
additional disk/PVC placement before deployment. Verify the shared controller,
bus, source and Sensors are Ready and old CronJob-owned Jobs are gone before
replaying work. No node increase or system-pool toleration is introduced.

The publisher retries transient API errors with curl's bounded retry behavior.
If publishing still fails after Terraform has applied, the Atlantis workflow
reports failure; the infrastructure apply has not been rolled back. Review the
actual cluster state before retrying. A stored signal survives watcher downtime;
manual replay remains available after exhausted retries or an active-run update.
There is no timer fallback and no claimed exactly-once delivery.

An operator can replay the chart-owned Job without reconstructing a manifest or
handling a token:

```sh
cluster=aks-example
kubectl -n cluster-onboarding get sensor "onboard-$cluster" \
  -o jsonpath='{.spec.triggers[0].template.k8s.source.resource}' |
  kubectl create -f -
```

`AlreadyExists` means the stable name remains: inspect it; do not delete an active
Job just to replay. Consult `workload-status-<cluster>` in `argocd` and shared logs.
Removing a profile removes its signal/Sensor/write permission; check active work
before deleting the corresponding cluster. Let active Jobs finish before changing
their immutable pod template. Existing workload packages continue reconciling
through their own Argo CD independently of new onboarding signals.
