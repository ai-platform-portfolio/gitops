# Code quality

[The Applications](../../templates/sonarqube.yaml) install credential delivery at wave 40,
SonarQube Developer at wave 60 and its configuration job at wave 70.

| File | Owning Application | Purpose |
| --- | --- | --- |
| `secrets.yaml` | configuration | ESO Password generators and immutable orphan credential targets |
| `database-secret.yaml` | sonarqube-credentials | Named read-only ESO access to the database password and public CA |
| `values.yaml` | sonarqube | External PostgreSQL JDBC connection and mounted CA |
| `database.yaml` | postgres | CNPG-managed SonarQube Database, before SonarQube starts |
| `network-policy.yaml` | configuration | Staged application/PostgreSQL rules; currently excluded from reconciliation |
| `configure-job.yaml`, `configure.sh` | sonarqube-configure | Admin password, permissions, base URL and Entra SAML settings |
| `networking.yaml` | routes | ListenerSet, HTTPS route and redirect after backend health |
| `namespace.yaml` | namespaces | Public-gateway label and pod-security settings |

ESO owns credential creation and delivery. Early configuration creates the role,
admin and monitoring Secrets; CNPG then reconciles the role and database. The
wave-40 credential Application copies only `sonarqube-role/password` and
`platform-ca/ca.crt` into `sonarqube-jdbc` with one-minute refresh. It grants no
access to the CA private key. Argo waits for the ExternalSecret before starting
SonarQube, then runs the existing configuration Job after backend health.

Generated targets use `CreatedOnce`, `immutable: true` and `creationPolicy: Orphan`.
ESO preserves the data of an existing Secret during adoption or ExternalSecret
recreation. The role/admin/monitoring Secrets become immutable; do not delete
these credentials beside retained data. The mutable JDBC target continues to
refresh the CA. No lifecycle script generates or copies SonarQube credentials.

SonarQube Developer requires a licence. The package configuration Job reads public application-specific Entra SAML
metadata; platform startup needs no SonarQube Graph lookup. Application credentials and database contents are disposable on teardown.

## Native Entra SAML

Management metadata lives in `sonarqube.saml.applicationId` and `metadataAppId`
in platform values. These identify the retained enterprise application; they are
not credentials. Workload onboarding continues supplying its Crossplane-managed
application identity through runtime metadata. Both use the same package-owned
public metadata fetch. The SAML certificate stays out of Terraform outputs and
bootstrap runtime discovery. No Entra object ownership changes in this handoff.


The retained `portfolio-sonarqube` enterprise application already owns the callback
`https://sonarqube.<DNS_ZONE>/oauth2/callback/saml`, the `api://<app-client-id>`
identifier, active signing certificate and `login`, `name`, `email` claims. Its
application roles map to `portfolio-sonar-admins` and `portfolio-sonar-analysts`;
these are Sonar groups, separate from the bootstrap AKS/Argo administrator group.
The existing assigned portfolio user has the administrator role. Existing Entra
resources are reused without adoption into Terraform or a second owner.

The configuration is a normal Argo-managed Job, named with a hash of its template
and rendered script. Configuration changes create a new Job; Argo prunes the old
completed Job after success, avoiding immutable-template patches. It must not be
a hook-only Application: hooks alone can compare Synced without an automatic
sync ever running. Keeping the completed Job gives Argo an observable success
condition. The Job rotates the initial admin credential, applies SAML and group
permissions, requires authentication, then reads back the SAML setting and verifies
a login redirect to Microsoft. Failure keeps the Application unhealthy.

Use **Microsoft Entra ID** on the login page. SAML users have no local password;
the generated local admin credential stays available for recovery and the
configuration Job. Scanners continue using their Sonar tokens. Interactive browser
login and role authorisation must still be tested; a healthy Kubernetes workload
alone is not evidence of successful SAML authentication.

Source/render checks establish ordering and credential policy. Existing-secret
adoption, fresh startup and browser SAML acceptance require a reviewed live run;
a source merge alone does not establish those outcomes.
