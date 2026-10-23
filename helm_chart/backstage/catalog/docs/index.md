# Platform developer portal

The catalog connects software to its owner, dependencies and operational guides.
Catalog data is eventually consistent; verify deployment state in the reconciler.

## Service requests

An authorised platform-team member can request a Python service. The template
opens a pull request to the operator-configured company repository. It does not
apply resources, merge PRs, build service images or grant repository access.
Existing services need explicit upgrades; template changes do not update copies.

## Access

Company OIDC login must resolve to an operator-managed catalog user. Readers can
browse catalog/docs. Only the platform-team group may start templates. Task logs
and cancellation are restricted to their creator. Unknown permissions are denied.
Catalog imports, arbitrary templates, cluster writes and MCP tools are disabled.

## Recovery

If the portal is down, use approved Git, Argo and Kubernetes access directly.
Existing workloads and reconciliation do not depend on the portal. Check the
Backstage readiness endpoint, PostgreSQL connectivity, authentication and Secret
freshness. A database migration can prevent an image-only rollback; test recovery
before upgrades. Portfolio data is disposable; production recovery needs tested
backups and explicit RPO/RTO.

## Company boundary

Catalog metadata, task logs and prompts can be sensitive. Use only approved
company-controlled OIDC and repository endpoints. No external AI, SaaS analytics
or runtime documentation builds are configured. Kubernetes egress is deny-by-
default and must explicitly allow approved internal endpoints. Public portfolio
CI must not receive private configs, secrets or runtime logs.
