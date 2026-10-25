output "cluster_name" {
  description = "Management cluster name."
  value       = module.aks.cluster_name
}

output "resource_group_name" {
  description = "Management resource group."
  value       = azurerm_resource_group.this.name
}

output "namespace" {
  description = "ArgoCD namespace."
  value       = module.argocd.namespace
}

output "argocd_url" {
  description = "Argo CD HTTPS endpoint, available after the GitOps foundations reconcile."
  value       = "https://${local.argocd_hostname}"
}

output "aks_connection" {
  sensitive = true

  value = {
    name           = module.aks.cluster_name
    resource_group = azurerm_resource_group.this.name
    server         = module.aks.host
    ca_data        = base64encode(module.aks.cluster_ca_certificate)
  }
}

output "platform_settings" {
  sensitive = true

  value = {
    cluster = {
      name = var.platform.cluster_name
      role = var.platform.cluster_role
    }

    git = {
      repository = var.platform.git_repository
      revision   = var.platform.git_revision
    }

    sonarqube_sso_display_name = var.platform.sonarqube_sso_display_name

    storage_containers = { for key, container in azurerm_storage_container.platform : key => container.name }

    argocd_identity = {
      client_id    = module.identities["argocd"].client_id
      principal_id = module.identities["argocd"].principal_id
    }

    runtime = {
      ASO_CLIENT_ID            = module.aso_identity.client_id
      ONBOARDING_CLIENT_ID     = module.identities["cluster_onboarding"].client_id
      ONBOARDING_PRINCIPAL_ID  = module.identities["cluster_onboarding"].principal_id
      CLUSTER_RESOURCE_GROUP   = azurerm_resource_group.this.name
      ARGOCD_HOSTNAME          = local.argocd_hostname
      AZURE_CLIENT_ID          = module.identities["platform"].client_id
      POSTGRES_AZURE_CLIENT_ID = module.identities["postgres"].client_id
      CROSSPLANE_CLIENT_ID     = module.entra_applications["crossplane"].client_id
      CROSSPLANE_PRINCIPAL_ID  = module.entra_applications["crossplane"].principal_id
      AZURE_DNS_RESOURCE_GROUP = var.dns.zone.resource_group_name
      AZURE_KEY_VAULT_URI      = data.azurerm_key_vault.platform.vault_uri
      AZURE_STORAGE_ACCOUNT    = data.azurerm_storage_account.platform.name
      DNS_ZONE                 = var.dns.zone.name
      AZURE_DNS_ZONE           = data.azurerm_dns_zone.existing.name
      ACME_EMAIL               = var.platform.acme_email
      CLUSTER_NAME             = var.platform.cluster_name
    }
  }
}
