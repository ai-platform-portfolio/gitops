# Workload cluster onboarding

A reviewed Backstage request creates two PRs: a private `cluster-deployments` Terraform root and a GitOps profile. Atlantis runs a saved plan/apply for the root. After AKS reports `Succeeded` and the GitOps profile merges, a dedicated management CronJob for that cluster completes onboarding. Atlantis has no Kubernetes credential.

The worker uses its own AKS Workload Identity and Azure RBAC. Management Argo reconciles `cluster-foundation`: child managed identities, federated credentials, scoped grants, containers and dedicated Entra registrations. The worker waits for those resources, then registers a CA-verified cluster Secret with management Argo, installs child Argo, seeds disposable application Secrets, installs the full local workload platform, prepares SonarQube after CNPG is ready and checks every current Application plus public DNS/TLS endpoints. `ConfigMap/argocd/workload-status-<name>` reports the phase. The worker marks the Argo registration `ready` only after those gates pass.

Backstage, Atlantis and the Entra Crossplane provider stay on management; each workload cluster receives the service packages (PostgreSQL, LGTM, Langfuse, SonarQube, DevLake, Testkube, Holmes and their controllers). Management Argo is the authenticated UI; child Argo has no public route or local admin login. Each workload service uses its own Entra registration. ASO and Crossplane retain single ownership of service dependencies. The public SonarQube SAML signing certificate comes from Entra's application federation metadata.

## One-time management activation

1. Merge the AKS module PR and GitOps PR; let the separate management deployment session apply the bootstrap role/FIC additions and reseed the platform root once. Confirm the management ASO, Crossplane, ESO and Argo health gates. This repository does not perform the live deployment.
2. Install a GitHub App on **both** `gitops` and the private `cluster-deployments` repo with the least permissions required for Backstage PR creation and Atlantis checks/comments. Put its credentials in the existing platform Key Vault as documented by the Backstage and Atlantis packages. Set the approved portal egress CIDRs, GitHub App ID/slug/team, and persistent state account role in `platform/values.yaml`.
3. Promote the built Backstage and worker images by immutable digest. Set `backstage.clusterRequests.enabled`, `atlantis.enabled` and `clusterOnboarding.enabled` to true in a reviewed GitOps PR. The default is disabled until credentials and images exist. Restrict the Atlantis GitHub App and team; plan executes code and its Azure identity receives subscription Contributor and role assignment authority. Require branch protection/CODEOWNERS, stale-review dismissal and trusted approval before plan as well as apply.
4. Check that the AKS API allowlist Key Vault secret includes the Atlantis egress address and the management worker/Argo egress address. Check regional and D-series family quota for the three D4as_v4 nodes and AKS surge. Set distinct, nonoverlapping CIDRs in the Backstage request.

## Cluster request

Submit **Request a workload AKS cluster** in Backstage. Review both generated PRs and the Atlantis plan. A trusted platform member comments `atlantis plan -d clusters/<name>` then `atlantis apply -d clusters/<name>` after approval. Merge the Terraform PR and then the GitOps profile PR. The worker discovers the new AKS through Azure, not Terraform output or state, and reconciles the platform. Watch `workload-status-<name>` and the management/child Argo Applications. The status is an observation, not evidence of a live deployment until the actual cluster has been checked.

The generated root reads API CIDRs from Key Vault; the value is sensitive in normal plans but retained in encrypted Terraform state. Never publish a raw real plan or put tenant/subscription IDs, private settings, credentials or the CIDRs in Git. Backend state remains in the persistent account on teardown.

## Removal

A workload profile or Terraform root must not be deleted as the first teardown step. Remove the child platform and its Argo registration, wait for the `foundation-<name>` Application's ASO/Crossplane finalizers to finish, and only then destroy AKS and its resource group with a reviewed Atlantis plan. The onboarding worker does not automate destructive teardown. Reusing the same cluster name is blocked until previous owners and DNS records are gone.

## Database ownership

The child platform follows the existing CNPG role/database/grant and application migration model. Record any future Atlas ownership transfer separately; do not let Atlas and CNPG/application migrations reconcile the same object. Atlas feature and licence coverage must be assessed before transferring ownership.
