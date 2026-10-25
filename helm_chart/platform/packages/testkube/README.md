# Standalone Testkube

The management root installs free standalone Testkube chart `2.14.2`, official
NATS chart `2.15.1` and S3Proxy `4.1.1`. No hosted account, license activation,
MongoDB, additional PostgreSQL server, local object store or new storage account
is required. Argo Rollouts is installed on every cluster; Argo Workflows is not
a Testkube dependency and is not added. Existing Crossplane remains the owner of
GitOps Entra application management; ASO owns the Azure resources below.

## Package layout

Files follow deployment stages rather than individual resource types:

| File | Contents / owner |
| --- | --- |
| `namespace.yaml` | Restricted namespace, shared namespaces stage |
| `configuration.yaml` | Generated credentials, internal TLS and networking, shared configuration stage |
| `azure-resources.yaml` | ASO-managed Azure dependencies |
| `database.yaml` | Dedicated database, shared PostgreSQL stage |
| `nats-values.yaml` | Independently ordered persistent broker release |
| `dependencies.yaml` | PostgreSQL credential delivery and storage adapter, wave 40 |
| `values.yaml` | Testkube Helm release |
| `checks.yaml` | Installation workflows, script ConfigMaps and ordered validation Jobs |
| `analysis.yaml` | Reusable release analysis template |

The two check scripts stay separate so reviewers can read executable logic without
YAML indentation. `templates/testkube.yaml` shows the Application dependency order.

## One-command deployment and sequencing

Use the repository's existing `make up` after setting up its documented backend,
authentication and tracked environment configuration. No manual SQL, generated
password, S3 bucket creation, service identity creation or Key Vault secret is
needed for Testkube. The Terraform caller must already be allowed to create
managed identities/federation and assign the scoped Azure roles, as with the
existing bootstrap identities. One command does not grant the caller privileges.

| Root wave | Dependency gate |
| --- | --- |
| -10 / 0 | Namespaces, ESO and foundational controllers |
| 5 | cert-manager and namespace-scoped certificate/credential reloader |
| 10 | Generated immutable application passwords, private certificates, ASO and CNPG operators; Rollouts controller after Gateway API |
| 20 | ASO identity, federation, account-scoped Blob role, two containers and native lifecycle policy, all Ready for their current generation |
| 30 | Shared PostgreSQL dedicated role/database and persistent mTLS NATS JetStream |
| 40 | ESO delivers verified PostgreSQL DSN/CA; stateless TLS S3Proxy becomes ready |
| 60 | Standalone API/executor; startup runs its own pinned Goose migrations |
| 65 | Schema-version check, then real workflow execution/artifact download and deliberate failure checks |
| 70 | Reusable Rollouts analysis template becomes available |

These stages use separate child Applications, gated by the root's current-source
Application health customization. Within `testkube-checks`, the schema Job is
wave 0 and execution Job wave 5. They are normal tracked Jobs, not hooks that
can be skipped on first installation. Package/configuration changes give them
new names so upgrades rerun the checks; pruning removes superseded Jobs. A
failure keeps this stage unhealthy and prevents subsequent root waves. Testkube
API readiness alone is insufficient because upstream logs migration errors as
warnings. The check requires applied Goose version `20260917090000` for this pin.

Terraform supplies only ASO's controller bootstrap authority: a dedicated identity
trusted by the management ASO service account, with Managed Identity Contributor
on the cluster resource group and Storage Account Contributor plus Role Based
Access Control Administrator on the existing application account. GitOps creates
the namespace credential from runtime context. ASO then creates the separate
Testkube storage identity, federation, role, containers and policy. The controller
has powerful scoped reconciliation rights; restrict writers to its namespace.
Atlantis retains its existing separately configured credential/permissions.

## Azure storage and retention

S3Proxy translates Testkube's S3 API into Azure Blob calls through the native
Azure SDK and `DefaultAzureCredential`. Its pod receives a projected federated
service-account token and the ASO-created managed identity's client ID; it uses
no Azure account key or client secret. Frontend AWSv4 credentials are separate,
ESO-generated application credentials. The adapter has ephemeral scratch space
and no PVC; only NATS adds one 1Gi managed disk. PostgreSQL reuses the existing
platform instance. This adds four steady pods (API, NATS, adapter, reloader), plus
transient validation and test Jobs; no extra nodes are prescribed.

ASO owns only the new `<cluster>-testkube-artifacts` and `<cluster>-testkube-logs`
containers in the existing account. It does not adopt existing Terraform-owned
containers or the persistent Terraform state backend. Blob Data Contributor is
account scoped, following the platform's scope policy. S3Proxy's bucket mapping
is **not an authorization boundary**; trusted callers holding its S3 credential
can exercise that account-level data permission. Keep the endpoint private.

`StorageAccountsManagementPolicy/platform-blob-retention` is the account's single
canonical lifecycle-policy owner. The target account had no lifecycle policy on
2026-10-25. Its rules delete block blobs only under the two Testkube container
prefixes after 14 days (configurable in GitOps). Azure evaluates lifecycle rules
asynchronously. Future account rules must be added to this same resource; do not
introduce another policy owner or overwrite an independently managed policy.
S3Proxy does not implement bucket lifecycle: Testkube's nonfatal lifecycle warnings
are expected, and Azure supplies retention instead. Execution metadata in
PostgreSQL is separate from artifact/log Blob retention.

## Authentication and security limits

Standalone has no native Entra application SSO, enterprise tenant RBAC or full
enterprise audit feature set. No public Testkube HTTPRoute or unauthenticated
dashboard is installed. Human CLI access uses Entra-authenticated AKS access and
Kubernetes authorization for `pods/portforward` or service proxy access. This
controls tunnel access; it does not create Testkube users or authorization roles.
An Envoy login alone cannot supply those missing application features.

The API permits ingress from its own trusted namespace and opted-in application
namespaces/pods carrying `gitops.platform/testkube-client: "true"`, plus monitoring.
There is no per-user authorization inside that network boundary. Application
release Jobs have no Kubernetes token and finite deadlines. Workflow defaults
use a dedicated tokenless service account, restricted security context, read-only
root filesystem and resource requests/limits. Defaults are not an admission
policy: only trusted authors may change workflow definitions or namespace labels.
Test egress is unrestricted so application tests can reach their targets.

Public HTTPS elsewhere in this repository uses an issuer-annotated ListenerSet
(for example `packages/argocd/networking.yaml` and
`packages/backstage/networking.yaml`); cert-manager generates its certificate,
and the HTTPRoute attaches to that listener. This package has no public route.
Its explicit private CA/server/client certificates in `configuration.yaml` are
for NATS mutual TLS and S3Proxy's internal TLS/PKCS12 keystore. Listener certificate
automation cannot issue NATS client credentials or replace these backend secrets.

Database, API-to-storage and NATS connections verify private CA certificates;
NATS additionally requires a client certificate. Reloader restarts affected
workloads on certificate/credential changes. The pinned upstream workflow-init
client hardcodes skipping certificate verification for presigned artifact URLs
(`cmd/testworkflow-init/data/client.go`); this cannot be fixed by Helm CA settings.
Private networking and signed URLs limit access, but this remains an upstream
security limitation, not verified end-to-end storage TLS for every client.
The standalone API/worker transport also remains private in-cluster HTTP/gRPC.
A stricter requirement needs an upstream fix or a separately reviewed deployment
mode; this free installation does not claim enterprise security certification.

## Application release gates

`ClusterAnalysisTemplate/testkube-workflow` takes `workflow` and `candidate-url`.
It runs the pinned CLI with `--watch`; failure or timeout fails the analysis.
Opt in the application namespace and analysis pod with the client label, then
reference it using `clusterScope: true` from an application-owned Rollout:

```yaml
analysis:
  templates:
    - templateName: testkube-workflow
      clusterScope: true
  args:
    - name: workflow
      value: my-application-tests
    - name: candidate-url
      value: http://my-application-canary.my-application.svc.cluster.local
```

The workflow belongs in the `testkube` namespace and declares string configuration
`candidateUrl`. Tests must target the candidate Service/preview endpoint, not a
weighted production route. Supply real assertions, credentials and retry/timeout
behavior per application. The reserved `platform-storage-smoke` and
`platform-failure-smoke` definitions are installation checks, not business tests.
See [Rollouts](../argo-rollouts/README.md) for traffic ownership and Argo diff rules.

This first installation is centralized on management and its reusable gate is
for that same cluster. Workload clusters receive Rollouts but no duplicate broker
or adapter. Cross-cluster Testkube access, authentication and application gate
wiring require a separate design; a management ClusterIP is not reachable merely
because another cluster has Rollouts installed.

## PostgreSQL permission handover to Atlas later

CNPG currently owns login role `testkube` and database `testkube`, owned by that
role. It has no superuser, CREATEDB, CREATEROLE or replication privilege. HBA
allows verified TLS only to its own database; ingress is limited to API and schema
check pods. `SKIP_DB_CREATION=true` avoids maintenance-database access. The role
owns application schema objects and needs DDL while upstream Goose startup
migrations remain enabled. ESO delivers its password and CNPG CA; the schema check
uses the same restricted account and cannot read other PostgreSQL Secrets.

Future Atlas work should inventory exact database/schema ownership, runtime DML,
migration DDL, sequence/default privileges and credential rotation. Plan a single
owner transfer from CNPG-managed roles/Database and any SQL grant reconciler,
check free/paid Atlas capabilities, and validate existing and newly created
objects. Moving permission management does not automatically authorize replacing
Testkube's Goose migration history or disabling startup migrations. No Atlas
ownership migration is performed here.

## Validation and teardown

`make validate` renders pinned upstream charts and both cluster roles, checks
secret stability, ownership, security settings and wave ordering. Local disposable
integration additionally exercises real CNPG migrations, ESO credentials, private
certificates, NATS mTLS and Testkube workflow/artifact behavior against Azurite
through the production S3Proxy Azure SDK backend. Emulator authentication uses
its published test key; this does not validate production workload federation,
Azure RBAC propagation, token renewal or Azure lifecycle execution. AKS remains
torn down; no cloud deployment was performed by this PR.

Remove application Rollouts before `make down`; the lifecycle fails closed if
any remain. Reverse waves remove analysis/checks/API/storage/broker, then ASO-owned
Azure resources while ASO and its credential still exist. Exact platform-tracked
Testkube/Atlantis resources are allowed; unrelated ASO resources block teardown.
Application data is disposable. The external Terraform state account/container
remains outside that lifecycle.

Sources: [Testkube chart](https://github.com/kubeshop/helm-charts/tree/main/charts/testkube),
[Testkube source release](https://github.com/kubeshop/testkube/releases/tag/2.14.2),
[S3Proxy Azure support](https://github.com/gaul/s3proxy/tree/s3proxy-4.1.1),
[ASO APIs](https://azure.github.io/azure-service-operator/reference/storage/v20230101/),
[Azure lifecycle](https://learn.microsoft.com/azure/storage/blobs/lifecycle-management-overview).
