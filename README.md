# GitOps

A federated Argo CD platform on Azure AKS. Each cluster reconciles the shared
platform independently; management onboards workload clusters.

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
