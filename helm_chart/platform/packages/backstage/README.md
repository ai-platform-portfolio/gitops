# Backstage platform package

An optional management-only developer portal reuses the existing CNPG cluster,
ESO/Key Vault and shared Envoy Gateway. It adds one application replica and a
separate `backstage` database/role, not another PostgreSQL instance, Redis,
storage account or Azure identity. The default remains disabled.

| File | Responsibility |
| --- | --- |
| `namespace.yaml` | Dedicated namespace with restricted pod admission |
| `database.yaml` | CNPG Database owned by the existing postgres Application |
| `secrets.yaml` | ESO reads existing Key Vault inputs and generates stable role/session credentials |
| `database-secret.yaml` | Named-secret RBAC and ESO delivery of the CNPG password/CA |
| `deployment.yaml` | Read-only non-root portal, Service and unprivileged ServiceAccount |
| `network-policy.yaml` | Default deny with explicit DNS, PostgreSQL and approved endpoint access |
| `networking.yaml` | Existing per-host ListenerSet and cert-manager ownership pattern |

The application source/build lives in [backstage/](../../../backstage/README.md).
Enablement, image promotion and endpoint choices live in GitOps values. There are
no Backstage-specific Terraform inputs/outputs or lifecycle-script steps.

## Enablement and lifecycle

1. Build the application and make its image accessible to AKS. Main CI publishes
   the public portfolio source to GHCR; use an internal registry for company data.
2. Populate the existing Key Vault entries below using approved administration.
3. Edit `backstage` in `helm_chart/platform/values.yaml` in the approved GitOps
   repository. Enable it with the built image digest and explicit company endpoint
   egress rules. Use a private GitOps source for company-specific configuration;
   do not publish private deployment inputs to this portfolio repository.
4. Argo reconciles the change on the existing cluster. No Terraform apply or
   `make up` is needed to enable, upgrade or reconcile Backstage.

For example (replace the digest and endpoint before enabling):

```yaml
backstage:
  enabled: true
  image: registry.company.internal/platform/backstage@sha256:REPLACE_WITH_IMAGE_DIGEST
  imagePullSecrets:
    - name: company-registry
  egress:
    - to:
        - ipBlock:
            cidr: 10.20.0.10/32
      ports:
        - protocol: TCP
          port: 443
```

The pull Secret must already exist in the backstage namespace when using a private
registry. Helm schema validation rejects mutable images, blanket `/0` egress,
invalid IPv4 CIDRs and invalid ports before Argo applies resources.

The existing `configuration` Application at wave 10 owns the Password generators
and their ExternalSecrets. Generated role/session Secrets use `CreatedOnce`,
immutable targets and orphan ownership so recreating an ExternalSecret does not
rewrite an existing credential. CNPG reconciles its non-superuser,
non-CREATEDB/non-CREATEROLE role and database at wave 30. The `backstage-credentials`
Application at wave 40 uses ESO's Kubernetes provider to read only
`postgres/backstage-role` and `postgres/platform-ca`, delivering password and CA
into `backstage/backstage-database`. The reader ServiceAccount has only named
Secret `get` access in postgres; ESO requests its token and the portal does not
use it. Store validation uses Kubernetes' standard authenticated-user permission
for self-subject reviews. No namespace-wide or cluster-wide Secret read is granted.

Argo waits for current CNPG Database health and ExternalSecret readiness before
starting the portal at wave 60 and its route at wave 80. There is no script-managed
Backstage readiness flag or imperative Secret copying. ESO refreshes the delivered
CA/password every minute; pods consume them through environment variables, so
restart Backstage after a deliberate credential/CA change. Do not delete immutable
role/session Secrets while the platform is running: deletion requests regeneration
and requires a coordinated credential rotation and application restart. Orphan
Secrets remain on feature disablement, and namespace/cluster teardown removes them.

The portal connects directly to `platform-rw` using verified TLS, bypassing
transaction pooling for migrations. Plugin data uses separate schemas within the
one Backstage database, with bounded per-plugin connection pools. Existing
SonarQube ownership and lifecycle remain unchanged.

| Key Vault entry | Value |
| --- | --- |
| `backstage-oidc-metadata-url` | Approved OIDC discovery document URL |
| `backstage-oidc-client-id` | Registered company OIDC client |
| `backstage-oidc-client-secret` | OIDC client secret |
| `backstage-catalog-org` | YAML documents defining company Users and Groups |
| `backstage-integrations` | App config YAML for approved integrations, or `{}` |

For example, operator-maintained organisation data uses this shape:

```yaml
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: platform-team
spec:
  type: team
  children: []
---
apiVersion: backstage.io/v1alpha1
kind: User
metadata:
  name: example-user
spec:
  profile:
    email: example-user@company.internal
  memberOf: [platform-team]
```

Use real identity data only in Key Vault, never this public repository. Restrict
who can edit that data: group membership grants template execution. Configure the
GitHub Enterprise integration only when approved:

```yaml
platform:
  gitopsRepoUrl: git.company.internal?owner=platform&repo=services
integrations:
  github:
    - host: git.company.internal
      apiBaseUrl: https://git.company.internal/api/v3
      apps:
        - appId: YOUR_APP_ID
          privateKey: |
            YOUR_PRIVATE_KEY
          allowedInstallationOwners: [platform]
```

Grant the App only required Contents and Pull requests write permissions on the
selected repository. Keep any required platform-specific permissions narrowly
scoped. No repository creation, administration, merge, cloud provisioning or
cluster mutation is performed by this portal. Test the exact company GitHub
Enterprise version/auth contract before enabling the template. Rotate auth/App
configuration through Key Vault then restart Backstage: configuration/environment
changes do not hot-reload. Restart the portal after ESO delivers
a changed CNPG CA. Do not rotate the database role independently of the
consumer Secret.

## Company perimeter and exposure

Runtime egress is denied by default except cluster DNS and the CNPG pods. The
optional `egress` list allows explicit destinations/ports for approved identity
and Git systems. IP policies cannot enforce hostnames, and shared IPs/NAT can
weaken a perimeter. Test CNI behaviour; use a company egress gateway/firewall when
hostname-aware enforcement is required. Do not add blanket internet access.

When enabled, networking follows this repository's existing **public** Gateway
pattern. OIDC and API authentication protect application data, but this is not a
private network boundary. Before handling company data, use the approved internal
Gateway/access perimeter and validate ingress, DNS, identity and egress together.
The frontend bundle is public under standard Backstage hosting; no private
metadata belongs in frontend configuration. The feature remains disabled until
its operator supplies and verifies the deployment inputs.

No Microsoft tenant/subscription IDs are introduced into Terraform outputs or
public deployment inputs. ESO retains Azure authentication; the Backstage pod has
no Azure identity, Kubernetes API token, cluster RBAC, Docker socket or external
AI endpoint. Do not attach third-party analytics or external model APIs to private
catalog, logs, documents or prompts. Self-hosting alone does not prove compliance.

## Azure Service Operator and Terraform decision

Backstage is an ordinary Kubernetes application. Its dependencies are GitOps-owned
through CNPG and ESO, using APIs already installed in the platform. It needs no
new Azure ARM resource, ASO CRD or Terraform change. The existing ASO installation
is preserved unchanged.

If TechDocs later moves to Azure Blob, define its container, workload identity,
federated credential and storage-account-scoped role assignments through ASO in
GitOps. The relevant Storage/ManagedIdentity/Authorization CRDs are already enabled
on main. Provisioning still requires the appropriate scoped ASO credentials.
Keep ownership singular; do not also declare those resources in Terraform.
Company OIDC client registration remains identity administration. Entra application
registrations use Microsoft Graph and are outside ASO's listed ARM resources;
identify an actual coverage gap before choosing an alternative implementation.

Sources: [ASO resources](https://azure.github.io/azure-service-operator/reference/),
[managed identity resources](https://azure.github.io/azure-service-operator/reference/managedidentity/v1api20230131/),
[ASO authentication](https://azure.github.io/azure-service-operator/guide/authentication/credential-format/),
[Backstage OIDC](https://backstage.io/docs/auth/oidc/),
[ESO stable generation](https://external-secrets.io/latest/api/externalsecret/#createdonce),
[ESO Kubernetes provider](https://external-secrets.io/latest/provider/kubernetes/).

## Cost, scaling and acceptance

Initial requests are 250m CPU/512Mi memory with a 1536Mi memory limit and one
replica. No node count/size change is requested. Measure actual capacity of the
whole stack before deployment; these requests are not evidence it fits or scales.
One replica, Recreate updates and shared single-instance CNPG are intentionally
non-HA. Each plugin has its own connection pool; budget aggregate connections
against the shared database before increasing replicas/plugins. Bundled docs
increase image churn; measure before adding object storage. Horizontal replicas,
HA PostgreSQL, isolated workers and separate search need measured justification.

Offline checks cover management/workload isolation, credential gates, CNPG resource
shape, immutable image references, least-privilege pod settings, permissions and
application builds. They do not prove live SSO, effective egress enforcement,
Gateway protection, Git publishing or cluster capacity.

Before claiming live readiness, record evidence for:

- Successful company login and rejection of an unregistered identity.
- Reader denied template execution; authorised owner sees only their task logs.
- PR created in the configured repository, with no merge or live deployment.
- TLS rejection with an incorrect CA and successful restricted-role migrations.
- Approved identity/Git connectivity and failed unapproved outbound access.
- Portal outage leaving workload reconciliation/services unaffected.
- Image upgrade, database migration/recovery and secret/CA rotation exercises.
- Resource usage and dependency/freshness behaviour under a disclosed test load.

Teardown deletes the portal before PostgreSQL through reverse Application waves.
The database uses the same retain-on-Database-deletion policy as SonarQube; full
portfolio teardown still deletes the disposable CNPG storage. There is no new
cross-teardown restore path. Rebuilding a disposable environment is not a tested
production backup/recovery strategy.
