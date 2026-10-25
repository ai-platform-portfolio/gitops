# HolmesGPT incident investigator

The optional `holmes` Application installs the free standalone HolmesGPT 0.45.0
chart at wave 50, after the LGTM backends and Kubernetes collector. It is one
private API Deployment and Service in the `holmes` namespace. Its operator,
Robusta platform, GitHub MCP server and Kubernetes remediation MCP server are
disabled. No database, persistent disk or public route is added. The package
is initially disabled while the private management Foundry account and model
in [bootstrap PR #48](https://github.com/ai-platform-portfolio/gitops/pull/48)
remain unapplied and the AKS cluster is torn down.

## Enablement requirements

The bootstrap proposal selects a UK South `gpt-4.1-mini` deployment named
`holmes` with regional `Standard` processing, a private endpoint and local API
keys disabled. The account name, resource group, endpoint and API version are
preconfigured in platform values, but must be verified against the actual
deployed account before enablement. A global deployment is not an equivalent
choice for the company boundary. HolmesGPT
itself is not Azure-specific: its incident toolsets work with other cloud and
Kubernetes data sources. This installation is restricted to management because
ASO currently runs there.

After applying bootstrap and validating the model, set `holmes.enabled: true`
and complete these remaining GitOps platform values:

| Value | Requirement |
| --- | --- |
| `model` | Preconfigured as `azure/holmes`; verify the deployed name. |
| `modelEndpoint` | Preconfigured HTTPS Foundry endpoint; verify private DNS resolution. |
| `modelApiVersion` | Preconfigured from current Holmes documentation; confirm with a live model request. |
| `modelCidrs` | Private endpoint address range. |
| `apiCidrs` | AKS Kubernetes API endpoint address range. |
| `entraCidrs` | Entra token endpoint ranges, or a tested private/proxy route. Maintain them as Microsoft changes them. |
| `azure.foundryResourceGroupName` / `foundryResourceName` | Preconfigured new management account; verify it exists before enabling. |
| `azure.identityResourceGroupName` | New package-owned group; do not point at a shared group. |
| `azure.asoClientIdSecret` | Existing scoped ASO provisioner client ID in Key Vault. |

The package's Azure resources are GitOps owned by ASO: a new resource group,
managed identity, AKS federated credential and `Cognitive Services OpenAI User`
assignment at the existing Foundry resource. ASO does not adopt the existing
Foundry resource. Its provisioner needs permission to create the new group and
identity and assign that role at the Foundry scope; verify its existing federation
and authority before enabling. ESO delivers its credential to the new namespace.
ASO exports the new identity client ID to a ConfigMap. The Holmes pod receives
that ID and a projected Azure-audience service account token; the pinned source
uses `DefaultAzureCredential` with `AZURE_AD_TOKEN_AUTH=true`. There is no model
API key. Verify the resulting pod gets a token and can query the approved model
before treating Helm/Argo health as an inference check.

The configuration stage also generates a random API key in `holmes-api-token`.
That key protects the Holmes API; it is separate from model authentication and
does not establish user identity. There is no native Entra SSO in the standalone
Holmes API, so it has no public route. Use approved Kubernetes access and
port-forward for initial tests, retrieving the API key under namespace Secret
RBAC. An Envoy login later needs a separate client and authorization review.

The Helm chart enables exception telemetry by default; `enableTelemetry: false`
and empty Sentry DSN disable that path. Internet, shell, Robusta, remediation,
GitHub and the alpha operator are explicitly disabled. The service account may
read workload kinds, logs and Argo applications across the cluster. It cannot
read Kubernetes Secrets or ConfigMaps or mutate resources. Its ingress policy
permits only same-namespace pods; API key middleware is enabled. Egress allows
DNS, existing LGTM backends, and selected AKS API/Foundry/Entra CIDRs. Verify
CNI handling of AKS API routing, private endpoint DNS and Entra token exchange
in a live test. IP ranges alone do not restrict hostname or TLS server identity;
company firewall and DNS policy must enforce approved destinations.

The toolsets query existing Mimir, Loki and Tempo directly. Those backends have
no native tenant authentication in this portfolio, so namespace/network
controls are part of access control. The AKS cluster is currently torn down;
no model call, cluster API call or alert delivery has been tested. The values
request 1 GiB and limit memory to 2 GiB. Measure real use before enabling it
on the small portfolio node pool.

## Alert-to-PR progression

This installation supports an investigator API with read-only evidence. It does
not install alert routing, Slack/Linear delivery or a coding worker. Add those
as a separately reviewed workflow: Alertmanager/Azure Monitor webhook to a
private queue; scoped investigation; evidence and secret filtering; a separate
repository worker with a limited GitHub App; issue, branch, tests and review PR;
then redacted links in Slack and Linear. Keep GitOps merge/deploy authority with
reviewers and Argo. Holmes Operator advertises GitHub PR creation but is alpha
in the pinned project's documentation and is not the production alert router.
The SaaS boundaries of Slack, Linear and GitHub need company approval before
sending incident details, source or raw logs to them.

Sources: [official Helm chart](https://github.com/HolmesGPT/holmesgpt/tree/0.45.0/helm/holmes),
[Azure Foundry workload identity](https://holmesgpt.dev/latest/ai-providers/azure-ai-foundry/),
[operator maturity](https://holmesgpt.dev/latest/operator/).
