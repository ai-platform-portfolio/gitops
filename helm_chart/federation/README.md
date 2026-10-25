# Workload cluster onboarding

The management cluster uses this chart's `workload-platforms` ApplicationSet to
seed a local `platform` Application on registered workload clusters. Each workload
cluster runs its own Argo CD installation and reconciles its own packages.

```text
Terraform root for each cluster
  └─ installs that cluster's Argo CD

Management Argo CD
  └─ workload-platforms ApplicationSet
       └─ onboard-<cluster> Application on management
            └─ platform Application on workload
                 └─ package Applications reconciled by workload Argo CD
```

The local platform root includes federation only when `cluster.role` is
`management`. Workloads do not create another fleet onboarding controller.
Terraform owns each Argo Helm release; Argo does not manage that same release.

## Fleet navigation

Use the management **Cluster overview** banner for the local `platform` plus one
`onboard-<cluster>` tile per registered workload. The onboarding tile's external
link opens that workload's Argo; its root node links there through
`argocd.argoproj.io/managed-by-url`. The destination Argo still authenticates and
authorises the user through its own Entra configuration.

**Settings → Clusters** lists registered connections. **ApplicationSets →
workload-platforms** shows generated onboarding Applications in a tree (an alpha
UI feature in stable Argo 3.5). Neither onboarding sync nor ApplicationSet health
is a complete workload package health report. Open each workload's local
`platform` Application to inspect its packages. See the
[overview guide](../../README.md#cluster-overview-and-package-navigation) for
inventory limits and the distinction between registered and running clusters.

## Registration and configuration

`helm_chart/scripts/cluster.py` registers a workload using a cluster Secret on
management:

- The `platform.ai-platform-portfolio/role: workload` label selects it.
- The `platform.ai-platform-portfolio/root` annotation contains the local root's
  Git source, cluster identity, runtime settings and SonarQube preparation flag.
- The registration uses a CA-verified API connection and workload identity.
  Application passwords are held in workload Kubernetes Secrets, not in this
  registration annotation.

The ApplicationSet reads that annotation and applies the root through
`helm_chart/charts/resources`. Management owns the onboarding Application;
workload Argo owns package reconciliation. If management becomes unavailable,
the existing workload root and package Applications continue reconciling locally.
Changing a registration still requires management to be available.

## Removal and portfolio limits

`preserveResourcesOnDeletion: true` and disabled onboarding pruning keep a deleted
registration from cascading into workload deletion. Teardown requires hub access,
detaches onboarding, then stops local syncs and deletes packages in reverse wave
order. A hub lookup error stops teardown.

The onboarding identity currently has Kubernetes cluster-admin on the workload.
The cluster module uses Entra authentication with Kubernetes RBAC; changing it to
Azure RBAC requires revisiting authorization. API access restrictions must allow
the management cluster's outbound addresses. Native provider/SDK authentication
for every lifecycle step remains future work; current scripts use Azure CLI.
