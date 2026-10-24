# Management cluster bootstrap

One Terraform root builds networking, AKS, map-driven managed identities,
disposable data containers and the initial ArgoCD Helm release. Subsequent
clusters have independent roots. Inputs belong in tfvars; declarations stay in
`variables.tf`, resource lookups and scope aliases in `main.tf`, authentication
in `provider.tf`.

`argocd-values.yaml` holds deployment settings. `argocd-configmap.yaml` is the
actual commented Argo resource installed by the same Helm release. The module
adds the managed identity's client ID to the configured Argo service accounts.

Azure CLI supplies authenticated account context. Tenant/subscription identifiers
are used internally without Terraform outputs. Existing DNS, Key Vault and the
platform storage account are looked up; this root owns their new data containers.
Those containers and their contents are deleted on teardown. There is no
cross-teardown restore or retained-credential store.

Run `make plan` from the repository root to preview Terraform changes, then
`make up` to initialize/apply infrastructure and deploy the full platform
chart. This includes Envoy Gateway, cert-manager, ExternalDNS, databases,
observability, federation and enabled applications. Argo is exposed through its
own issuer-annotated ListenerSet and HTTPRoutes.

The URL is `https://<argocd.dns_label>.<dns.zone.name>`, built directly from the
required tfvars label and zone. Terraform supplies the same URL to
Argo's ConfigMap and the GitOps route. The lifecycle checks DNS ownership before
seeding and waits for a current certificate, current Gateway DNS and verified
HTTPS. Existing unowned exact A/AAAA/CNAME records cause an error instead of being
adopted. No port forwarding is required. Read the initial admin password locally
from `argocd-initial-admin-secret`; never commit it or print it in shared logs.
For Argo CLI access through the HTTPRoute, use `--grpc-web`.

Envoy terminates TLS and forwards HTTP to Argo's ClusterIP service. Argo's namespace
receives only the shared gateway label from GitOps, with `Prune=false,Delete=false`
so package cleanup cannot delete its own controller. Terraform retains ownership
of the Argo Helm release. No duplicate certificate manifest or wildcard certificate
is created; cert-manager generates the per-host certificate from the ListenerSet.

State uses the external Azure Blob backend configured by ignored
`backend.local.hcl`; keep backend details and private tfvars out of Git. Use
`make down` for ordered package cleanup before destroying infrastructure. See the
[configuration and lifecycle guide](../helm_chart/README.md) for prerequisites,
backend setup and validation.
