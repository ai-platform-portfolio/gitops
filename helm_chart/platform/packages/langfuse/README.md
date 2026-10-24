# LLM observability

[The Application](../../templates/langfuse.yaml) installs Langfuse at wave 40.
`values.yaml` references the cluster-local credential Secrets prepared before
Argo reconciliation, including encryption keys and credentials for bundled
PostgreSQL, Redis, ClickHouse and S3-compatible storage. These bundled stores are
separate from the standalone PostgreSQL/ClickHouse packages and are disposable.
They are not protected by CNPG backups. Existing Secrets are reused in the running
cluster to keep application credentials stable.

`networking.yaml` contains the application-owned HTTPS ListenerSet, route and
redirect. The routes Application applies these after backend readiness;
cert-manager issues a per-host certificate.

## Native Microsoft sign-in

On management, `langfuse.sso` in platform values enables the native Entra OIDC
provider. `disableUsernamePassword` remains false for recovery until the profile
email prerequisite and interactive Entra login are verified; set it true afterwards. The callback is
`https://langfuse.<DNS_ZONE>/api/auth/callback/azure-ad`. Workload clusters do not
install the management identity controllers or inherit this identity; their
Langfuse password login stays enabled until a child-specific SSO design is added.

`identity.yaml` requests a Crossplane `OIDCApplication`. Its composition owns the
single-tenant registration, enterprise principal and credential. Direct assignments
come from the `users` map: keys describe users and values are their Entra **object
IDs**, not email addresses. Removing an entry removes that managed assignment.
Direct user assignments do not require premium group-assignment licensing. The
principal requires assignments, so other tenant users are not granted access by
virtue of sharing the tenant. Out-of-band assignments must not be added: Crossplane
manages the declared assignment resources, not an authoritative inventory of all
assignments on the principal.

The provider requests an email ID-token claim, but that does not create a missing
Entra profile email. Every assigned user needs a usable `mail` attribute. The
current portfolio user's attribute was empty during research; updating that profile
requires the user's explicit approval and remains a deployment prerequisite.
The controller has no permission to edit user profiles.

The composition delivers `langfuse-sso` directly as a Kubernetes Secret with
`client-id`, `client-secret` and `tenant-id`. No lifecycle Graph script, Key Vault
copy, ESO bridge or extra identity setup command is used. Argo's wave/health gates
install the controllers and reconcile the identity before Langfuse starts. Later
assignment changes reconcile from GitOps without another `make up`.

The credential expires after `credentialValidityDays`. A scoped Reloader restarts
the web Deployment when its SSO Secret changes; automatic time-based rotation is
not configured. See the [controller package](../crossplane/README.md) for ownership,
permissions, credential expiry and teardown behavior. The generated registration
is disposable with this cluster; it is not a retained application credential.

Account linking is enabled only for this single-tenant, assignment-required
provider. Verify the email matches the existing Langfuse account. Entra sign-in
does not grant organisation/project membership or administrator privileges;
Langfuse retains those authorisation decisions.

After deployment, verify `/api/auth/providers` advertises Azure AD, sign-in redirects
to Microsoft, the intended local password recovery setting is respected, the assigned user reaches the
intended organisation, and an unassigned user is rejected. SDK/API-key ingestion
keeps its native authentication; no interactive Envoy login gate is added to it.

Sources: [Langfuse authentication](https://langfuse.com/self-hosting/security/authentication-and-sso),
[pinned implementation](https://github.com/langfuse/langfuse/blob/v4.50.0/web/src/server/auth.ts).
