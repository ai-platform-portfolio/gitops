# Code quality

[The Applications](../../templates/sonarqube.yaml) install SonarQube Developer at
wave 60 and its configuration job at wave 70, once `sonarqubeReady` is true.

| File | Owning Application | Purpose |
| --- | --- | --- |
| `values.yaml` | sonarqube | External PostgreSQL JDBC connection and mounted CA |
| `database.yaml` | postgres | CNPG-managed SonarQube Database, before SonarQube starts |
| `network-policy.yaml` | configuration | Restrict traffic to the application and PostgreSQL |
| `configure-job.yaml`, `configure.sh` | sonarqube-configure | Admin password, permissions, base URL and Entra SAML settings |
| `networking.yaml` | routes | ListenerSet, HTTPS route and redirect after backend health |
| `namespace.yaml` | namespaces | Public-gateway label and pod-security settings |

`helm_chart/scripts/credentials.py` prepares the role/admin/monitoring Secrets and copies
PostgreSQL's current CA into the JDBC Secret. The lifecycle then enables the
managed role in `../postgres/cluster.yaml`, this Database, application and routes.
Argo checks Database reconciliation before advancing to the SonarQube wave.

SonarQube Developer requires a licence. The existing Entra service principal and
active public SAML certificate must be discoverable by the authenticated lifecycle
identity. Application credentials and database contents are disposable on teardown.

## Native Entra SAML

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
