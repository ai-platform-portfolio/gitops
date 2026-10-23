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
