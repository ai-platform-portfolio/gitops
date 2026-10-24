# Platform configuration and lifecycle

This repository demonstrates a federated Argo CD platform on Azure AKS. Management
onboards workload clusters; each cluster's own Argo CD reconciles its packages
independently. Terraform provisions infrastructure and installs each initial Argo
release. Application configuration is organised by package, following the original
Flux layout while making the Argo ownership and dependency order explicit.

Start with [the platform guide](platform/README.md) for the package layout and
installation sequence, then [federation](federation/README.md) for cluster
onboarding and failure behavior. [Bootstrap](../bootstrap/README.md) describes the
Terraform handoff.

```text
gitops/
  bootstrap/           Terraform and the initial Argo release
  helm_chart/
    backstage/         Developer portal application source and image build
    platform/          Explicit Applications, package configuration and root chart
    federation/        Management ApplicationSet and onboarding guide
    charts/resources/  Minimal chart for manifest-only child Applications
    scripts/           Lifecycle, credential preparation and render validation
    tests/             Credential and teardown regression tests
  Makefile             Repository-root bootstrap and lifecycle commands
```

Run commands from the repository root. To trace PostgreSQL, read
`helm_chart/platform/templates/postgres.yaml`, then follow its references into
`helm_chart/platform/packages/postgres/`. The operator, Barman and database remain
independently ordered Applications. Other packages follow the same path.

`make up` provisions the management AKS cluster, installs ArgoCD and reconciles
the platform packages. This is a disposable portfolio deployment: `make down`
removes packages, infrastructure and Terraform-managed data containers.
Credentials and application data are not restored into the next deployment.

## Configuration

Review the tracked `bootstrap/terraform.tfvars` deployment configuration and
complete its API allowlist. Make always uses this file unless you explicitly pass
`TFVARS=/path/to/settings.tfvars`; no local filename takes priority. Principal IDs,
resource names, API CIDRs and email are configuration metadata. Credentials,
private keys, backend configuration and Terraform state stay out of Git.

- `azure`, `network`, `aks`, `dns`: management infrastructure and the existing DNS zone.
  `aks.admin_group` creates an Entra security group owned by the authenticated
  Terraform principal, with explicit admin members. AKS consumes the group's
  module output. No Azure roles are assigned.
- `identities`: one module loop over identities, federated service accounts and
  roles. Blob permissions use the existing storage account's ID as scope.
- `platform.key_vault`: the existing vault used by External Secrets for application secrets.
- `platform.storage`: one existing account with Terraform-managed PostgreSQL and
  observability containers. Use fresh names; existing containers are not automatically
  adopted. Teardown deletes the managed containers and their contents.
- `argocd`: chart pin, deployment values, actual commented Argo ConfigMap and
  `dns_label` for its HTTPS hostname, combined directly with `dns.zone.name`.
  The label and ConfigMap filename are required inputs specified in tfvars.

The initial configuration uses one Standard_D4s_v5 system node and one
Standard_D4s_v5 application node. PostgreSQL and its pooler each use one instance;
there is no synchronous standby requirement. This is a non-HA portfolio starting
point; measure usage before scaling. Use a supported Kubernetes 1.34–1.36 release
for the pinned CNPG operator.

## Remote Terraform state

The root uses an Azure Blob backend with Microsoft Entra authentication, not
storage account keys. Backend settings are separate from deployment tfvars
because Terraform reads them during `init`, before provider data sources.

Set up ignored `bootstrap/backend.local.hcl` with the existing state storage
account, container and a unique state key:

```hcl
storage_account_name = "YOUR_STATE_ACCOUNT"
container_name       = "tfstate"
key                  = "gitops/management.tfstate"
resource_group_name  = "YOUR_STATE_RESOURCE_GROUP"
```

The selected backend's subscription/tenant can also be set in this ignored file.
Otherwise Make supplies account context from the authenticated CLI session through
`ARM_SUBSCRIPTION_ID` and `ARM_TENANT_ID`. Override the file path with
`BACKEND_CONFIG=/path/to/backend.hcl`. The identity needs Blob Data Contributor
access to the state container for state reads, writes and lease locking.

`make init` passes this file to Terraform. It does not automatically migrate local
state or overwrite another deployment's state; review any migration explicitly.
CI uses `terraform init -backend=false` and does not access live state. The state
account/container are external to this root and survive application teardown.

## Management lifecycle

Install Terraform >=1.5, Azure CLI, kubectl, kubelogin and Python 3. Authenticate
with Azure CLI and select the intended subscription. CI uses the same session,
established through federated identity or managed identity.

```sh
make plan       # Preview bootstrap infrastructure and Argo CD changes
make up         # Apply AKS/Argo and reconcile the full platform
make down
```

`make up` runs Terraform init and apply automatically, creating the
containers, networking, AKS, identities and initial Argo release. It then seeds
the full platform root, including Envoy Gateway, cert-manager, ExternalDNS,
databases, observability, federation and enabled application packages.
`make plan` is a separate preview; `make up` automatically approves its
apply and does not consume a saved plan file.

The lifecycle prepares
Kubernetes Secrets, waits for PostgreSQL, copies its current CA for SonarQube and
enables the remaining packages. Existing Secrets are reused during repeated
`make up` runs so encryption keys and passwords stay stable within the cluster. Missing
credentials beside existing volumes cause an error rather than rotating keys.

Teardown disables automated sync and terminates active root operations before
reading the complete child Application inventory. It then disables child syncs,
cancels queued operations and waits for running operations to terminate. Applications
are deleted in reverse wave order, keeping operators until their custom resources
disappear. Terraform then destroys infrastructure and disposable data containers.
There is no final-backup gate, Blob credential record or automatic restore.
The existing storage account, DNS zone, Key Vault and Entra applications remain.
State uses the external Azure Blob backend; run lifecycle commands serially.

## Other clusters

This bootstrap directory is for management only. Subsequent clusters use their
own Terraform roots. The management ApplicationSet can onboard registered
workload clusters by seeding independent local platform roots; their own Argo
controllers continue reconciling if management becomes unavailable.

Registration uses workload identity, CA-verified API access and Kubernetes RBAC.
The current onboarding identity has cluster-admin on the child. Allow the hub's
outbound addresses through workload API access restrictions, and detach workload
registrations before teardown. This directory provides no workspace-based
multi-cluster provisioning or workload tfvars template.

## Packages and ingress

Explicit Applications pin 17 baseline releases on management (16 on workloads): cert-manager, External Secrets, Envoy Gateway,
ExternalDNS, Loki, Mimir, Tempo, Grafana, Kubernetes Monitoring/Alloy,
OpenTelemetry, ClickHouse operator/cluster, CloudNativePG, Barman, Langfuse and
SonarQube Developer, plus Azure Service Operator on management. Argo Application health gates dependency waves.

[ASO](platform/packages/azure-service-operator/README.md) starts without a global
Azure credential and enables resource-group, managed-identity, role-assignment
and storage CRDs for future service installations. Azure operations require
a separately provisioned namespace credential; existing infrastructure stays in
Terraform. Teardown refuses to stop ASO while Azure custom resources remain.

Loki, Grafana, Tempo and Mimir values share `helm_chart/platform/packages/lgtm/values.yaml`.
Each explicit Application selects its values by name. Other packages keep their
own values, including ClickHouse's independently ordered operator and database.
Package-specific ingress and policies live beside their values; the shared
configuration and routes Applications preserve readiness gates.

CNPG owns SonarQube's role and Database declaratively. Argo waits for the current
Database generation before installing SonarQube. The lifecycle prepares its
credentials and current CA without executing SQL provisioning commands.

The shared Gateway accepts application-owned HTTPS ListenerSets. Their issuer
annotations let cert-manager generate individual hostname certificates.
Cert-manager starts after Envoy installs Gateway API CRDs. ExternalDNS enables
ListenerSet discovery with matching RBAC. Argo routes attach at wave 25. Application routes follow
backend health at wave 80; the full lifecycle omits Sonar routes until its
credentials/CA are ready.

Barman and the daily PostgreSQL backup schedule demonstrate backups while the
cluster exists. Archives live in a disposable Terraform-managed container and
are deleted on teardown. Langfuse's bundled stores and standalone ClickHouse
also lose their data on teardown. SonarQube Developer needs a licence.

Mimir's final-source eviction webhook override produces an expected
`RepeatedResourceWarning`; other webhook failure policies remain unchanged.
Private Git forks need repository credentials on each Argo installation.

## Validation

```sh
python3 -m venv .venv
.venv/bin/pip install -r helm_chart/scripts/requirements.txt
make validate PYTHON=.venv/bin/python
```

`cluster.py` handles seeding, onboarding, readiness and ordered cleanup.
`credentials.py` manages cluster-local Secrets; `azure.py` reads CLI account
context without printing identifiers. `validate.py` renders the platform and all
pinned charts, checking stable secret references, CNPG schemas, ListenerSets and
the actual Argo ConfigMap. Unit tests cover credential reuse and teardown error
handling. CI also validates Terraform through shared ops workflows; it deploys
no resources. The archived Flux setup remains on `feature/migrate-away-from-flux`.

## Optional Terraform self-service

[Atlantis](platform/packages/atlantis/README.md) adds an eighteenth pinned release on management when explicitly enabled. It uses GitHub App credentials, a dedicated Azure workload identity, a 5 GiB disk and human-approved PR applies. Bootstrap/recovery stay outside this runner. Complete its prerequisites and verify permissions before enabling it.
