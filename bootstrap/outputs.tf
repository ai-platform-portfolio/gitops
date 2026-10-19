output "cluster_name" {
  description = "Management cluster name."
  value       = module.aks.cluster_name
}
output "resource_group_name" {
  description = "Management resource group."
  value       = azurerm_resource_group.this.name
}
output "oidc_issuer_url" {
  description = "Cluster issuer for federated identities."
  value       = module.aks.oidc_issuer_url
  sensitive   = true
}
output "dns_settings" {
  sensitive   = true
  description = "Runtime identity and configuration for GitOps ExternalDNS."
  value = {
    zone_name                 = data.azurerm_dns_zone.existing.name
    resource_group            = var.dns.zone.resource_group_name
    subscription_id           = data.azurerm_client_config.current.subscription_id
    tenant_id                 = data.azurerm_client_config.current.tenant_id
    client_id                 = module.dns_identity.client_id
    service_account_namespace = var.dns.service_account.namespace
    service_account_name      = var.dns.service_account.name
    txt_owner_id              = var.aks.name
  }
}

output "namespace" {
  description = "ArgoCD namespace."
  value       = module.argocd.namespace
}
output "port_forward_command" {
  description = "Run against the management context to access ArgoCD at https://localhost:8080."
  value       = "kubectl -n ${module.argocd.namespace} port-forward svc/argocd-server 8080:443"
}
