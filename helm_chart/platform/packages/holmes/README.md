# HolmesGPT incident investigator

The optional `holmes` Application installs the free standalone HolmesGPT 0.45.0
chart at wave 50, after the LGTM backends and Kubernetes collector. It is one
private API Deployment and Service in the `holmes` namespace. Its operator,
Robusta platform, GitHub MCP server and Kubernetes remediation MCP server are
disabled. No database, persistent disk, Azure resource or public route is added.
The package is initially disabled because the platform has no approved model
endpoint. `make up` does not create one.

## Enablement requirements

Set `holmes.enabled: true` and supply these GitOps values after the company's
model and network review:

| Value | Requirement |
| --- | --- |
| `model` | Provider/model identifier understood by the pinned Holmes release, e.g. `openai/<deployment>` for an OpenAI-compatible endpoint or `azure/<deployment>` for Azure OpenAI. |
| `modelEndpoint` | Approved HTTPS inference URL. It must resolve to an address allowed below. |
| `modelApiVersion` | API version where the selected provider requires it; otherwise empty. |
| `modelKeyVaultSecret` | Name of the existing Key Vault secret holding its API key. ESO copies it to `holmes-model`; the value never enters Git. |
| `modelCidrs` | Narrow CIDRs for the model's private endpoint or approved egress target. |
| `apiCidrs` | Narrow CIDRs for the AKS Kubernetes API endpoint. |

The shared Key Vault `ClusterSecretStore` and ESO must be healthy before Holmes
starts. The configuration stage also generates a separate random API key in
`holmes-api-token`. Neither key is a user identity. There is no native Entra
SSO in the standalone Holmes API, so the service has no public route or portal.
Use approved Kubernetes access and port-forward for initial tests, retrieving the
API key from the namespace Secret under Kubernetes RBAC. Adding an Envoy login
later would need a separate application/client authorization review.

The Helm chart enables exception telemetry by default; `enableTelemetry: false`
and empty Sentry DSN disable that path. Internet, shell, Robusta, remediation,
GitHub and the alpha operator are explicitly disabled. The service account may
read named workload kinds, logs and Argo applications across the cluster. It
cannot read Kubernetes Secrets or ConfigMaps or mutate resources. Its ingress
policy permits only same-namespace pods; API key middleware is enabled. Egress
allows DNS, the existing LGTM namespace, the selected Kubernetes API CIDRs and
model CIDRs. Verify CNI handling of AKS API routing and private endpoint DNS in a
live test; rendered policies alone do not establish an exfiltration boundary.

The toolsets query existing Mimir, Loki and Tempo directly. Their backends have
no native tenant authentication in this portfolio; namespace/network controls
are therefore part of access control. The AKS cluster is currently torn down,
so no model call, cluster API call, browser sign-in or alert delivery has been
tested. The values use 1 GiB requested and 2 GiB limited memory; measure real
usage before enabling on the small portfolio node pool.

## Alert-to-PR progression

This installation supports an investigator API with read-only evidence. It does
not install alert routing, Slack/Linear delivery or a coding worker. Add those
as a separately reviewed workflow: Alertmanager/Azure Monitor webhook to a
private queue; scoped investigation; evidence and secret filtering; a separate
repository worker with a limited GitHub App; issue, branch, tests and review PR;
then redacted links in Slack and Linear. Keep GitOps merge/deploy authority with
reviewers and Argo. Holmes Operator advertises GitHub PR creation but is alpha
in the pinned project's documentation and is not the production alert router.
The external SaaS boundaries of Slack, Linear and GitHub need company approval
before sending incident details, source or raw logs to them.

Sources: [official Helm chart](https://github.com/HolmesGPT/holmesgpt/tree/0.45.0/helm/holmes),
[model providers](https://holmesgpt.dev/latest/ai-providers/azure-ai-foundry/),
[operator maturity](https://holmesgpt.dev/latest/operator/).
