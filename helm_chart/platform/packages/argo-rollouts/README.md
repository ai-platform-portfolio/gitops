# Progressive delivery

The platform installs Argo Rollouts chart `2.43.8` (controller `v1.10.1`) on
each cluster at wave 10, after Envoy installs Gateway API CRDs at wave 0.
Application Rollouts, routes and analysis resources must be applied after this
controller becomes healthy. The root's existing Application health check provides
the gate; putting unrelated Applications in the same wave does not order them.

Two replicas use leader election, a disruption budget and preferred host spreading.
They do not require additional nodes. The Gateway API plugin `v0.17.0` is copied
from a digest-pinned multi-architecture image by a restricted init container.
The controller loads the local binary; startup does not download executables from
GitHub. Upstream supplies writable `plugin-bin` and `/tmp` volumes; the container
root filesystem stays read-only. HTTPRoute and GRPCRoute are supported.

The chart's unrelated traffic-provider permissions and aggregate user roles are
disabled. The controller still has upstream cluster-wide workload, Job and Secret
permissions, including Secret reads for analysis arguments: this is a trusted
platform controller, not a tenant security boundary. Application authors able to
create analysis Jobs can exercise those Jobs' service accounts; limit that access
with Kubernetes/Argo RBAC and namespace admission. Give test Jobs a dedicated
service account and disable token mounting unless their tests need the API.

The unauthenticated standalone dashboard is disabled. Use Argo CD's Rollout view
through its native Entra SSO, or the Rollouts CLI through Kubernetes RBAC. This
does not require an Envoy authentication overlay or another Entra application.

Controller metrics are scraped by the existing Grafana collector and sent to
Mimir. Discovery is restricted to the Rollouts namespace, does not send a bearer
token and does not depend on Prometheus Operator CRDs. An ingress NetworkPolicy
allows only the monitoring namespace to reach the metrics port. Egress remains
available for Kubernetes reconciliation and application-specific analysis providers.

## Application ownership

Installing the controller does not convert Deployments or enable release gates.
Each application must own its Rollout, stable/canary Services, route and
AnalysisTemplates together. Do not let an existing Deployment and a Rollout manage
the same pods. Plan adoption per application, including its workloadRef or explicit
Deployment-to-Rollout migration.

For a canary, configure `trafficRouting.plugins.argoproj-labs/gatewayAPI` with the
application's route name, and reference the stable/canary Services. Point tests at
the candidate Service or preview hostname, rather than the shared weighted route,
so a successful check cannot accidentally test only the stable version.

Argo must preserve fields owned by Rollouts. For example, on the **application's**
Application, substitute its exact resource names and namespace:

```yaml
spec:
  ignoreDifferences:
    - group: ""
      kind: Service
      name: application-stable
      namespace: application
      jsonPointers:
        - /spec/selector/rollouts-pod-template-hash

    - group: ""
      kind: Service
      name: application-canary
      namespace: application
      jsonPointers:
        - /spec/selector/rollouts-pod-template-hash

    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: application
      namespace: application
      jsonPointers:
        - /metadata/labels/rollouts.argoproj.io~1gatewayapi-canary
      jqPathExpressions:
        - '.spec.rules[].backendRefs[] | select(.name == "application-stable" or .name == "application-canary") | .weight'

  syncPolicy:
    syncOptions:
      - RespectIgnoreDifferences=true
```

Keep other route configuration visible to Argo. These exceptions cover weighted
canaries; header-routing rules require additional, narrowly scoped ownership
rules. Do not globally ignore `.spec.rules` or Service selectors.

Use blocking analysis for promotion: wait for the test execution's terminal
result, set a deadline and propagate failure/nonzero exit status. A successful
submission to a testing API is not a passed test. Treat test-system errors,
timeouts and missing metrics as blocking or inconclusive, not success. Combine
functional checks with application latency/error metrics and minimum sample
requirements. Controller metrics alone do not measure application health.

An Argo CD PostSync hook can run post-deployment checks but is not a production
traffic gate. A Rollouts abort changes rollout/traffic state; it does not revert
the Git image revision. Revert or promote a corrected revision through Git before
resuming normal reconciliation. Database migrations must remain compatible with
the previous application version throughout the rollout.

## Removal and validation

Remove application Rollouts before `make down`; the lifecycle refuses to remove
the controller while Rollouts remain in any namespace. Application finalizers,
analysis Jobs and testing workloads must finish before their controllers,
credentials, databases and gateway dependencies are removed. The platform removes
packages in reverse wave order.

`make validate` renders the pinned chart and checks CRDs, plugin configuration,
security, leader election, Gateway API permissions, monitoring and installation
order. Live AKS acceptance must also exercise a candidate test pass, a deliberate
failure, a timeout, an abort and traffic returning to the stable version, with
Argo self-heal enabled. Rendering and a Healthy controller are not evidence that
an application's release gates work.

References: [Rollouts analysis](https://argoproj.github.io/argo-rollouts/features/analysis/),
[Gateway API plugin installation](https://rollouts-plugin-trafficrouter-gatewayapi.readthedocs.io/en/stable/installation/),
[plugin quick start](https://rollouts-plugin-trafficrouter-gatewayapi.readthedocs.io/en/stable/quick-start/).

The [standalone Testkube guide](../testkube/README.md#application-release-gates) describes the management-local workflow analysis gate and cross-cluster limits.
