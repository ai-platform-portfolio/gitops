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
