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

output "port_forward_command" {
  description = "Run against the management context to access ArgoCD at https://localhost:8080."
  value       = "kubectl -n ${module.argocd.namespace} port-forward svc/argocd-server 8080:443"
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

  precondition {
    condition     = !var.platform.atlantis.enabled || (contains(keys(var.identities), "aso") && !contains(keys(var.identities), "atlantis"))
    error_message = "Enabling Atlantis requires identities.aso and no identities.atlantis entry. ASO owns the application identity; Terraform owns only its provisioner."
  }

  value = {
    cluster = {
      name = var.platform.cluster_name
      role = var.platform.cluster_role
    }

    git = {
      repository = var.platform.git_repository
      revision   = var.platform.git_revision
    }

    # ASO creates the application identity; Terraform supplies only the
    # operator's initial authority. No Atlantis client ID comes from Terraform.
    atlantis = var.platform.atlantis

    sonarqube_sso_display_name = var.platform.sonarqube_sso_display_name

    storage_containers = { for key, container in azurerm_storage_container.platform : key => container.name }

    argocd_identity = {
      client_id    = module.identities["argocd"].client_id
      principal_id = module.identities["argocd"].principal_id
    }

    runtime = {
      ASO_AZURE_CLIENT_ID      = try(module.identities["aso"].client_id, "")
      AZURE_LOCATION           = var.azure.location
      AZURE_CLIENT_ID          = module.identities["platform"].client_id
      POSTGRES_AZURE_CLIENT_ID = module.identities["postgres"].client_id
      AZURE_DNS_RESOURCE_GROUP = var.dns.zone.resource_group_name
      AZURE_KEY_VAULT_URI      = data.azurerm_key_vault.platform.vault_uri
      AZURE_STORAGE_ACCOUNT    = data.azurerm_storage_account.platform.name
      DNS_ZONE                 = coalesce(var.platform.domain_name, data.azurerm_dns_zone.existing.name)
      AZURE_DNS_ZONE           = data.azurerm_dns_zone.existing.name
      ACME_EMAIL               = var.platform.acme_email
      CLUSTER_NAME             = var.platform.cluster_name
    }
  }
}
