# Local platform reconciliation

Each cluster's Argo CD reconciles this Helm root against its own Kubernetes API.
The root renders explicit child Applications in `templates/`. Start with the
Application for a package, then follow its file references into `packages/`.

For example, `templates/postgres.yaml` declares the CNPG operator at wave 10,
Barman at wave 20 and PostgreSQL resources at wave 30. The resource definitions
and chart values live together in `packages/postgres/`. These remain independent
Applications so operator health can gate creation of its custom resources.

## Installation

`make up` applies Terraform and seeds this complete
chart as one local root Application. All standard packages are installed; optional
Atlantis and Backstage packages follow their existing enablement settings. There
is no foundations-only profile.

Every cluster renders the same platform packages except Backstage, which remains
management-only. Workload cluster infrastructure still needs its own Terraform
root and per-cluster runtime values, including an ASO provisioner identity,
Crossplane identity and AKS OIDC issuer. Rendering the chart does not create
those foundations or verify private routes to shared Azure services.

`packages/argocd/` owns the Argo ListenerSet, certificate annotation and HTTPRoutes.
It adds the gateway label to the existing Argo namespace, with deletion/pruning
disabled for that namespace. The Helm release and canonical URL remain
Terraform-owned. See the top-level README for hostname selection and DNS checks.

## Reading the configuration

| Location | What to look for |
| --- | --- |
| `templates/<package>.yaml` | Application names, pinned charts, namespaces, sync waves and exact values/configuration files |
| `packages/<package>/` | Package values, namespaces, networking and custom resources |
| `templates/configuration.yaml` | Early configuration assembled from package-owned files |
| `templates/namespaces.yaml` | Namespaces assembled from package-owned files before installation |
| `templates/routes.yaml` | Application-owned ListenerSets and routes attached after backend health |
| `templates/_helpers.tpl` | The common sync/retry policy; sources and ordering stay in explicit Application YAML |
| `values.yaml` | Cluster role, Git source and the runtime handoff from the lifecycle script |

Chart pins, destinations and waves are edited in the Application YAML rather than
a separate package catalog. `values.yaml` holds only the cluster/Git/runtime
handoff, the SonarQube preparation flag and optional Atlantis/Backstage GitOps settings.

The small `../charts/resources` chart passes manifest YAML through to Argo.
It lets the root template runtime settings once and supply the resulting resources
to a child Application with its own health and deletion lifecycle. It does not
select packages or generate Applications.

## Ownership and ordering

Files are grouped by the package they describe. Their owning Application is
explicitly listed in each template. The `configuration`, `namespaces` and `routes`
Applications are shared readiness stages: keeping these stages preserves the
existing ownership and installation/deletion order while making their source
files easier to find.

| Wave | Applications | Dependency |
| --- | --- | --- |
| -10 | namespaces | Namespaces exist before resources and credentials are consumed |
| 0 | reflector, envoy-gateway, external-secrets, clickhouse-operator, federation | Foundational controllers; federation is installed on each cluster |
| 5 | cert-manager, testkube-reloader | Envoy has installed Gateway API and ListenerSet CRDs |
| 10 | argo-rollouts, cloudnative-pg, configuration, azure-service-operator | Gateway API exists for Rollouts; foundation controllers deliver issuers, secret stores and the Atlas token |
| 20 | atlas-operator, plugin-barman-cloud, external-dns, gateway, testkube-azure, optional atlantis-azure | Operators/configuration are available and the Atlas token is Ready |
| 25 | argocd-route | Shared Gateway, issuer and DNS controller are ready |
| 30 | postgres, clickhouse, loki, grafana, tempo, mimir, testkube-nats | Database operators, backup plugin and shared secrets are ready |
| 40 | langfuse, opentelemetry-collector, k8s-monitoring, optional Atlantis and backstage-credentials, testkube-storage | Backend services are ready |
| 50 | holmes | LGTM and Kubernetes telemetry are available; live model and identity validation still required |
| 60 | sonarqube, optional backstage, testkube | PostgreSQL roles, databases and consumer credentials are ready |
| 65 | testkube-checks | Schema version, artifact roundtrip and failure propagation must pass |
| 70 | sonarqube-configure, testkube-analysis | SonarQube is healthy before its configuration job runs |
| 80 | routes | Application backends are ready before public routes are attached |

Waves belong to child Applications in one local root. The custom Application
health check in `../../bootstrap/argocd-configmap.yaml` waits for current-source sync
and health before the parent advances. ApplicationSet creation order alone does
not provide these dependency gates.

The lifecycle initially supplies `sonarqubeReady: false`, prepares credentials,
waits for PostgreSQL and copies its CA. It then sets the flag to true. That enables
SonarQube's managed role, Database, application, configuration job and routes.
The role and Database are applied by the `postgres` Application before wave 60.

## Making a change

1. Open the relevant `templates/<package>.yaml` and check its chart pin and wave.
2. Edit its referenced `packages/<package>/values.yaml` or manifest.
3. For a new resource, add an explicit file reference to its owning Application.
4. From the repository root, run `make validate PYTHON=.venv/bin/python` using
   the [validation setup](../README.md#validation).

LGTM values are grouped in `packages/lgtm/values.yaml`, with separate keys and
Applications for Loki, Grafana, Tempo and Mimir. Other packages keep their own
values files. ClickHouse operator/database and CNPG operator/Barman/database keep
separate Applications and waves even when their templates share a file.

Runtime values come from Terraform outputs and authenticated account context.
Deployment metadata lives in the tracked `bootstrap/terraform.tfvars`; tenant and
subscription context stays outside it. Credentials are Kubernetes Secrets reused
within the running cluster. Application data and backup containers are disposable on teardown.

[Azure Service Operator](packages/azure-service-operator/README.md) describes its
namespace credentials, Azure ownership boundary and consumer lifecycle.

Atlantis is an opt-in package on each cluster; see its [setup and operation guide](packages/atlantis/README.md). Its ASO-owned dependencies and configuration live in GitOps, with no Atlantis Terraform inputs or PostgreSQL dependency.

## Optional Backstage

The management-only [Backstage package](packages/backstage/README.md) is disabled
by default. Configure it directly in GitOps `values.yaml`. Its database belongs
to the existing postgres Application. ESO generates stable credentials in early
configuration, then the wave-40 backstage-credentials Application delivers the
password/CA before the wave-60 portal and wave-80 route. It adds no PostgreSQL
server, Azure infrastructure or Backstage-specific bootstrap/lifecycle handoff.

[Atlas](packages/atlas/README.md) installs the operator with an Atlas Cloud bot
token delivered from Key Vault. No database migrations are configured yet;
existing database ownership stays with CNPG and application migration systems.

[Reflector](packages/reflector/README.md) provides annotation-driven Secret and
ConfigMap replication. Installation alone enables no copies; future sources must
explicitly restrict destination namespaces.

[HolmesGPT](packages/holmes/README.md) is staged as a private investigator on
each cluster. Model, Kubernetes API and Entra egress stay denied until verified
destinations are configured; the ASO provisioner credential is also pending.

[Argo Rollouts](packages/argo-rollouts/README.md) supplies progressive delivery
and the Gateway API traffic plugin on every cluster. Application-owned analysis
and testing integration are separate from installing its controller. See the
[production testing review](testing-review.md) for remaining dependencies.

[Standalone Testkube](packages/testkube/README.md) documents the one-command dependency gates, Azure storage, private access and later Atlas permission handover. [Testing review](testing-review.md) records scope and limits.

[DevLake](packages/devlake/README.md) adds free DORA analytics on each cluster,
sharing its local CNPG and Grafana. Crossplane owns Entra; the stable release uses an Envoy
SSO overlay with retained backend Basic authentication. Credentials at wave 40
move management Grafana to wave 45; configuration and policy/collection run at
70/75, before routes and the final policy/certificate readiness Job. No production
service is inferred from management syncs. Configure service projects in GitOps.
