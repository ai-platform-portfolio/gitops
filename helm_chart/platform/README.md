# Local platform reconciliation

Each cluster's Argo CD reconciles this Helm root against its own Kubernetes API.
The root renders explicit child Applications in `templates/`. Start with the
Application for a package, then follow its file references into `packages/`.

For example, `templates/postgres.yaml` declares the CNPG operator at wave 10,
Barman at wave 20 and PostgreSQL resources at wave 30. The resource definitions
and chart values live together in `packages/postgres/`. These remain independent
Applications so operator health can gate creation of its custom resources.

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
| 0 | envoy-gateway, external-secrets, clickhouse-operator, federation | Foundational controllers; federation exists only on management |
| 5 | cert-manager | Envoy has installed Gateway API and ListenerSet CRDs |
| 10 | cloudnative-pg, configuration, azure-service-operator (management only) | Foundation controllers can reconcile issuers, secret stores and external secrets |
| 20 | plugin-barman-cloud, external-dns, gateway, optional atlantis-azure | Operators/configuration are available |
| 30 | postgres, clickhouse, loki, grafana, tempo, mimir | Database operators, backup plugin and shared secrets are ready |
| 40 | langfuse, opentelemetry-collector, k8s-monitoring, optional Atlantis and backstage-credentials | Backend services are ready |
| 60 | sonarqube, optional backstage | PostgreSQL roles, databases and consumer credentials are ready |
| 70 | sonarqube-configure | SonarQube is healthy before its configuration job runs |
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

Runtime values come from Terraform outputs and authenticated account context,
not checked-in deployment identifiers. Private deployment inputs remain in
ignored tfvars. Credentials are Kubernetes Secrets reused within the running
cluster. Application data and backup containers are disposable on teardown.

[Azure Service Operator](packages/azure-service-operator/README.md) describes its
namespace credentials, Azure ownership boundary and consumer lifecycle.

Atlantis is an opt-in management package; see its [setup and operation guide](packages/atlantis/README.md). Its ASO-owned dependencies and configuration live in GitOps, with no Atlantis Terraform inputs or PostgreSQL dependency.

## Optional Backstage

The management-only [Backstage package](packages/backstage/README.md) is disabled
by default. Configure it directly in GitOps `values.yaml`. Its database belongs
to the existing postgres Application. ESO generates stable credentials in early
configuration, then the wave-40 backstage-credentials Application delivers the
password/CA before the wave-60 portal and wave-80 route. It adds no PostgreSQL
server, Azure infrastructure or Backstage-specific bootstrap/lifecycle handoff.
