# SonarQube Server Developer Edition

Official stable chart `2026.5.1002` installs SonarQube Server `2026.5.2` with
`edition: developer`. The chart selects the matching image. Values override only
service naming, JDBC/TLS, existing secrets, the CA mount.
The password hook is patched to fail on HTTP errors and accept the retained admin
password after restore and sets the public server URL through the settings API. Other chart values use upstream defaults.

## Licensing and access

A SonarQube Developer evaluation or commercial licence is required for paid
analysis. A Gitar trial does not license SonarQube Server. Install first, then
request a Developer evaluation at https://www.sonarsource.com/get-started/server/.
Activate it under Administration > Configuration > License Manager; use the
server ID shown under Administration > System when requested. The trial lasts
14 days. The chart and application installation do not themselves activate it.
Never put licence keys, passwords or scanner tokens in this public repository.

The existing public Envoy Gateway serves `https://sonarqube.<DNS_ZONE>` with TLS.
This is authenticated public access, not an internal-only fintech deployment.
The admin password is generated and retained outside AKS. Retrieve it locally
from the `password` key in Secret `sonarqube-admin` in namespace `sonarqube`.
Do not put its value in terminal logs, issues or chat.

Developer provides native branches/PRs, quality gates/profiles, taint analysis,
secrets detection and supported YAML, Shell and GitHub Actions analysis. GitHub
integration, project creation, scanner tokens and required merge checks must be
configured after licence activation. It does not include Enterprise-only AI
CodeFix, Advanced Security/SCA, portfolios or compliance reporting.

Gitar is separate. We are evaluating its hosted Pro trial; custom self-hosted
Gitar deployment is offered under Enterprise and must be agreed with the vendor.
Gitar Pro sends repository context and CI logs to external processing. It is not
installed in this AKS cluster and is not covered by the Sonar licence.

## PostgreSQL and lifecycle

`make up` restores/prepares the existing CloudNativePG platform first, then
prepares a dedicated UTF-8 `sonarqube` database and non-superuser owner. JDBC uses
`platform-rw.postgres.svc.cluster.local` with certificate and hostname validation;
the current PostgreSQL CA is copied before Sonar starts. Network policies allow
Sonar's application pods to reach the primary on TCP 5432.

Generated database/admin/monitoring credentials are retained in
`lifecycle/sonarqube.json` in the same private Azure Blob container used by
PostgreSQL backups, accessed using Azure RBAC. They are verified before SQL is
changed. An existing database without its credential record fails closed.

`make down` stops/uninstalls Sonar before the final verified PostgreSQL backup
and cluster deletion. Its namespace, hook resources and ephemeral indexes are
removed with the cluster. Chart-default persistence is disabled; indexes rebuild
from the restored database. Sonar configuration and history are stored in that
backup. Credentials remain in Blob; no Sonar-specific Azure disk remains.

Keep the database hostname/name and retained database state stable to preserve
instance identity. Sonar licence validity must be checked after recovery; changes
to server ID may require licence transfer/reactivation. A cluster rebuild does
not renew or restart the evaluation period. Offline licence activation requires
manual file exchange; it cannot be promised as an unattended lifecycle step.

The upstream sysctl init container requires a privileged namespace to configure
Elasticsearch prerequisites. HTTPS egress permits licensing and DevOps APIs;
this policy does not restrict outbound destinations by hostname. A production
fintech deployment should use internal routing and controlled egress instead.
