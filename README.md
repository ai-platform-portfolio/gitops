# GitOps

A federated Argo CD platform on Azure AKS. Each cluster reconciles the shared
platform independently; management onboards workload clusters.

Terraform owns each cluster's initial Argo CD installation. The shared platform
Helm chart defines local package Applications and their readiness gates; the
management ApplicationSet seeds independent workload roots. See the guides below
for ownership, configuration and teardown behavior.

- [bootstrap/](bootstrap/README.md): Terraform infrastructure and the initial
  Argo CD installation.
- [helm_chart/](helm_chart/README.md): platform and federation charts, package
  configuration, lifecycle scripts and tests.

Start with the [platform guide](helm_chart/platform/README.md) for package
ownership and dependency ordering, or the
[federation guide](helm_chart/federation/README.md) for workload onboarding.

Run `make plan`, `make up`, `make down` and `make validate` from the repository
root. The [configuration and validation guide](helm_chart/README.md) covers
private inputs, prerequisites and portfolio limits.

Validation covers lifecycle tests, rendered Kubernetes resources and pinned
upstream charts. Live installation and multi-cluster failure behavior have not
yet been validated in this workspace; passing CI does not establish deployment
readiness.

Optional developer portal: [Backstage application](helm_chart/backstage/README.md) and
[deployment package](helm_chart/platform/packages/backstage/README.md). It reuses
CNPG and remains disabled until an image and company-controlled identity/network
configuration are supplied.
