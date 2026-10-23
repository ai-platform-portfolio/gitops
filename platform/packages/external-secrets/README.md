# Application secrets from Key Vault

[The Application](../../templates/external-secrets.yaml) installs External
Secrets before the shared configuration stage. `values.yaml` configures the
`eso-akv-wi` service account with the platform workload identity.

`secret-store.yaml` defines the Azure Key Vault ClusterSecretStore. The
`configuration` Application applies it and LGTM's Grafana ExternalSecret before
Grafana starts. Key Vault URI and tenant context are runtime inputs.

Generated application passwords are separate cluster-local Secrets prepared by
`scripts/credentials.py`; they are reused during the cluster lifetime.
