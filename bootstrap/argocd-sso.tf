# Entra applications use Microsoft Graph, outside ASO's Azure ARM resources.
# This registration belongs to the Terraform-owned initial Argo installation.
resource "azuread_application" "argocd" {
  display_name            = var.argocd.sso.display_name
  sign_in_audience        = "AzureADMyOrg"
  owners                  = [data.azurerm_client_config.current.object_id]
  group_membership_claims = ["SecurityGroup"]

  web {
    redirect_uris = ["https://${local.argocd_hostname}/auth/callback"]
  }

  public_client {
    redirect_uris = ["http://localhost:8085/auth/callback"]
  }
}

resource "azuread_service_principal" "argocd" {
  client_id = azuread_application.argocd.client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

# The same projected token can authenticate the SSO app and the existing Argo
# managed identity. Keep the server's identity annotation for child-cluster access.
resource "azuread_application_federated_identity_credential" "argocd" {
  application_id = azuread_application.argocd.id
  display_name   = "argocd-server"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = module.aks.oidc_issuer_url
  subject        = "system:serviceaccount:argocd:argocd-server"
}
