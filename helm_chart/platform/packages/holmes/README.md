# HolmesGPT incident investigator

The optional `holmes` Application installs the free standalone HolmesGPT 0.45.0
chart at wave 50, after the LGTM backends and Kubernetes collector. It is one
private API Deployment and Service in the `holmes` namespace. Its operator,
Robusta platform, GitHub MCP server and Kubernetes remediation MCP server are
disabled. No database, persistent disk or public route is added. The package
is enabled in GitOps for each cluster's next deployment.
Model authentication and connectivity require live validation; Helm/Argo health
alone does not establish a working investigation service. Current deployment
state belongs in lifecycle evidence rather than these package instructions.

## Enablement requirements

The merged bootstrap declares a UK South `gpt-4.1-mini` deployment named
`chat` with regional `Standard` processing, a private endpoint and local API
keys disabled. The account name, resource group, endpoint and API version are
preconfigured in platform values, but must be verified against the actual
deployed account before enablement. A global deployment is not an equivalent
choice for the company boundary. HolmesGPT
itself is not Azure-specific: its incident toolsets work with other cloud and
Kubernetes data sources. ASO and Holmes install on each cluster.

Each instance investigates its own cluster. Its Kubernetes tools use the local
API, and its LGTM URLs are local services. The instances do not aggregate data
across clusters. Each cluster needs its own ASO authority, private Foundry route,
OIDC issuer and verified connectivity before Holmes can operate there.

`holmes.enabled` is already `true`. Complete these remaining GitOps values and
validate the deployed model before using the service:

| Value | Requirement |
| --- | --- |
| `model` | Preconfigured as `azure/chat`; verify the deployed name. |
| Model endpoint | Derived from bootstrap's `FOUNDRY_ACCOUNT_NAME`; verify private DNS resolution. |
| `modelApiVersion` | Preconfigured from current Holmes documentation; confirm with a live model request. |
| `modelCidrs` | Address inputs for the staged model egress policy; currently unenforced. |
| `apiCidrs` | Address inputs for the staged Kubernetes API egress policy; currently unenforced. |
| `entraCidrs` | Address inputs for the staged Entra egress policy; currently unenforced. |
| `azure.foundryResourceGroupName` | Account name comes from bootstrap runtime; verify it exists before enabling. |

The package's Azure resources are GitOps owned by ASO: a managed identity in
the existing management resource group, an AKS federated credential and a
`Cognitive Services OpenAI User` assignment scoped to the bootstrap-owned
Foundry account. ASO does not adopt the group or Foundry account. The existing
ASO bootstrap identity has Managed Identity Contributor on that group and a
Foundry-account-scoped RBAC administrator grant so ASO can create the role
assignment. Its client ID comes from the bootstrap runtime context; the
configuration stage delivers it as a Kubernetes Secret rather than requiring
a second Key Vault copy.
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
read Kubernetes Secrets or ConfigMaps or mutate resources. API key middleware is
enabled and no public route is configured. Kubernetes NetworkPolicies are
currently excluded from reconciliation: staged same-namespace ingress and
DNS/LGTM/API/Foundry/Entra egress rules provide no enforcement. Namespace placement
and CIDR settings do not establish isolation. See the
[bootstrap status](../../../../bootstrap/README.md#kubernetes-networkpolicy-status).

The toolsets query existing Mimir, Loki and Tempo directly. Those backends have
no native tenant authentication in this portfolio. Namespace placement currently
provides no NetworkPolicy access boundary. Verify access, model calls and alert
delivery separately before treating the investigator as ready. The values
request 1 GiB and limit memory to 2 GiB. Measure real use before relying on it
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
