# LLM observability

[The Application](../../templates/langfuse.yaml) installs Langfuse at wave 40.
`values.yaml` references the cluster-local credential Secrets prepared before
Argo reconciliation, including the application encryption key and credentials
for bundled PostgreSQL, Redis, ClickHouse and S3-compatible storage.

`networking.yaml` contains the application-owned HTTPS ListenerSet, route and
HTTP redirect. The `routes` Application applies them after backend readiness;
cert-manager issues the per-host certificate. The public hostname is a runtime
DNS-zone input.

Bundled stores are distinct from standalone PostgreSQL/ClickHouse releases.
Their data is disposable on teardown and is not protected by CNPG backups.
Existing Secrets are reused within the running cluster to keep credentials stable.

## Native Microsoft sign-in

`langfuse.sso` in the platform values owns the explicit Entra user allowlist,
credential lifetime and account-linking choice. The chart disables local password
login when enabled and uses its native Azure AD/Entra OIDC provider. The callback
is `https://langfuse.<DNS_ZONE>/api/auth/callback/azure-ad`. A token email claim is
required; each allowlisted Entra user must have a nonempty `mail` profile attribute.
The lifecycle fails before changing the registration if a user cannot be resolved
or lacks that attribute. Matching existing accounts are linked only for the
explicitly assigned users of this single-tenant application. Organisation/project memberships remain
Langfuse-owned and are preserved for linked accounts. Entra login does not grant
Langfuse administrator or organisation-owner privileges automatically.

The configuration Application creates a non-secret settings ConfigMap and an ESO
ExternalSecret. `make up` reads the current GitOps settings, then reconciles a
marked `portfolio-<cluster>-langfuse` application and enterprise principal through
Microsoft Graph. Only the configured users may sign in; direct user assignments
do not require premium group-assignment licensing. Changing the allowlist requires
both a GitOps merge and `make up` to reconcile the Graph assignments.

This narrow lifecycle step covers an operator gap: ASO's ARM resources do not
manage Graph application registrations. It uses the existing authenticated Azure
CLI context, with no Terraform dependency, output handoff or service variables in
bootstrap. The Terraform Entra module remains available for Terraform-owned
foundations; this service registration has one owner, the GitOps lifecycle.
An existing unmarked application with the same name is rejected, not adopted.
The operator needs authority to create/manage applications, enterprise principals
and user assignments, to look up those users, and to write the platform Key Vault.
Azure subscription Contributor alone does not provide the Graph permissions.

The client secret, app client ID and tenant context are one JSON secret named
`<cluster>-langfuse-oidc` in the existing platform Key Vault. ESO extracts its three
fields into the `langfuse-sso` Kubernetes Secret. No client secret is passed on a
process command line, printed, committed, put into Argo values, or written into
Terraform state. Temporary request/secret files are private and removed. The Entra
registration and its Key Vault credential are retained across disposable cluster
teardown; this does not retain or restore application data.

A valid credential is reused. `make up` renews within 14 days of expiry using the
configured lifetime (30–365 days), waits for ESO delivery, and restarts an existing
web deployment when a new credential is issued because environment variables do
not hot-reload. Previous credentials remain valid during delivery/rollout and can
be retired after verifying the replacement. A failed Key Vault write revokes only
the newly generated credential. After an interrupted renewal, verify ESO delivery
and restart the web deployment if it still holds the previous environment.

After deployment, `/api/auth/providers` must advertise Azure AD, sign-in must
redirect to Microsoft, and the password form must be disabled. Verify the assigned
user can access their existing organisation, and an unassigned Entra user is
rejected. SDK/API-key authentication is unchanged; no interactive Envoy gate is
added to ingestion routes.

Sources: [Langfuse native authentication](https://langfuse.com/self-hosting/security/authentication-and-sso),
[pinned Azure provider implementation](https://github.com/langfuse/langfuse/blob/v4.50.0/web/src/server/auth.ts).
