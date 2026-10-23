# GitOps

`make up` provisions the management AKS cluster, installs ArgoCD and reconciles
the platform packages. This is a disposable portfolio deployment: `make down`
removes packages, infrastructure and Terraform-managed data containers.
Credentials and application data are not restored into the next deployment.

## Configuration

Copy `bootstrap/terraform.tfvars` to ignored `bootstrap/local.tfvars`. Complete
the AKS version, Entra admin group, public API CIDRs and service references.
Make selects the local file when present; `TFVARS=/path/to/settings.tfvars`
overrides it. Keep private values and Terraform state out of Git.

- `azure`, `network`, `aks`, `dns`: management infrastructure and the existing DNS zone.
- `identities`: one module loop over identities, federated service accounts and
  roles. Blob permissions use the existing storage account's ID as scope.
- `platform.key_vault`: the existing vault used by External Secrets for application secrets.
- `platform.storage`: one existing account with Terraform-managed PostgreSQL and
  observability containers. Use fresh names; existing containers are not automatically
  adopted. Teardown deletes the managed containers and their contents.
- `argocd`: chart pin, deployment values and the actual commented Argo ConfigMap.

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
make plan
make up
make down
```

Terraform creates the containers, networking, AKS, identities and initial Argo
release. The lifecycle prepares Kubernetes Secrets, seeds the local platform
root, waits for PostgreSQL, copies its current CA for SonarQube and enables the
remaining packages. Existing Secrets are reused during repeated `make up` runs
so encryption keys and passwords stay stable within the cluster. Missing
credentials beside existing volumes cause an error rather than rotating keys.

Teardown suspends the root and waits for its active operation to finish before
reading and suspending the complete child Application inventory. Applications
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

The catalog pins 16 releases: cert-manager, External Secrets, Envoy Gateway,
ExternalDNS, Loki, Mimir, Tempo, Grafana, Kubernetes Monitoring/Alloy,
OpenTelemetry, ClickHouse operator/cluster, CloudNativePG, Barman, Langfuse and
SonarQube Developer. Argo Application health gates dependency waves.

Loki, Grafana, Tempo and Mimir values share `platform/files/values/lgtm.yaml`,
selected by `valuesGroup: lgtm`. Each retains its own Application. Other values
stay separate, including ClickHouse's operator and database releases.

CNPG owns SonarQube's role and Database declaratively. Argo waits for the current
Database generation before installing SonarQube. The lifecycle prepares its
credentials and current CA without executing SQL provisioning commands.

The shared Gateway accepts application-owned HTTPS ListenerSets. Their issuer
annotations let cert-manager generate individual hostname certificates.
Cert-manager starts after Envoy installs Gateway API CRDs. ExternalDNS enables
ListenerSet discovery with matching RBAC. Routes follow backend health; initial
bootstrap omits Sonar routes until its credentials/CA are ready.

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
.venv/bin/pip install -r scripts/requirements.txt
make validate PYTHON=.venv/bin/python
```

`cluster.py` handles seeding, onboarding, readiness and ordered cleanup.
`credentials.py` manages cluster-local Secrets; `azure.py` reads CLI account
context without printing identifiers. `validate.py` renders the platform and all
pinned charts, checking stable secret references, CNPG schemas, ListenerSets and
the actual Argo ConfigMap. Unit tests cover credential reuse and teardown error
handling. CI also validates Terraform through shared ops workflows; it deploys
no resources. The archived Flux setup remains on `feature/migrate-away-from-flux`.
