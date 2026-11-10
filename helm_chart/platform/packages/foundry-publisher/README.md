# Foundry publisher identity

This management-only package creates one AKS service account and an ASO-owned
user-assigned identity with a federated credential for short-lived Entra tokens.
Its Foundry Project Manager grant is scoped to the configured project. Agent
repositories do not register themselves in GitOps; their release Jobs use the
unchanged chart in `application-library/agent-factory` and this shared service
account.

The Foundry project's separate bootstrap-owned identity receives `AcrPull` on
the existing ACR through `bootstrap/terraform.tfvars`. It pulls hosted agent
images; the publisher identity creates agent versions. Keep those permissions
separate. The package provides no application image, agent registration or
long-lived credential. Its ConfigMaps contain only IDs and the project URL.

Wait for the ASO UserAssignedIdentity, FederatedIdentityCredential and
RoleAssignment to report Ready, then verify the actual Azure role before
running a publisher Job. A Ready condition from before an account replacement
is not proof that a grant still exists.

# Read-only diagnostic broker

The same shared package also installs a separate `foundry-diagnostic-broker`
service account and federated identity. Its project-scoped `Foundry User` grant
can invoke the hosted agent without the publisher's version-management role.
The Kubernetes ClusterRole permits only read verbs for Deployments, Rollouts, Pods, Pod
logs and Events; it cannot read Secrets/ConfigMaps or mutate resources. The
reusable Job chart creates a RoleBinding only in the declared incident
namespace and removes it with the short-lived Job release. The broker also
checks that namespace at runtime. Mimir access currently depends on
the existing unauthenticated in-cluster endpoint; staged NetworkPolicies are
not enforced. This package adds no ingress, public route or network change.
