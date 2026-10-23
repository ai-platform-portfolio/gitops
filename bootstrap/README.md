# Cluster bootstrap

One Terraform root builds networking, AKS, map-driven managed identities and
ArgoCD. The reusable identity module also owns scoped roles. Inputs are grouped
in `terraform.tfvars` and ignored `local.tfvars`; declarations are in `variables.tf`,
scope lookups alongside the modules in `main.tf`, and authentication in `provider.tf`.

`argocd-values.yaml` contains deployment settings. `argocd-configmap.yaml` is the
actual Argo configuration resource, installed as part of the same Helm release.

The authenticated Azure CLI session supplies tenant/subscription context.
Terraform uses that context internally; outputs contain package connection
settings without account identifier values or the tenant-bearing OIDC issuer URL.
The platform account, its cluster containers, existing DNS, Key Vault and Entra
apps remain outside the cluster state's ownership.

Use Make from the repository root for container preparation and infrastructure
provisioning. The platform layer adds metadata adoption, health checks and
verified shutdown backups; once packages are installed, use that full lifecycle.
Direct `terraform destroy` bypasses those checks. State is currently local and
must be retained and protected.

See the [root guide](../README.md) for configuration and lifecycle details.
After obtaining cluster credentials, access Argo with:

```sh
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Retrieve the initial password locally from `argocd-initial-admin-secret`; do not
commit it or put it into logs.
