output "cluster_id" {
  description = "AKS resource ID for controlled onboarding."
  value       = module.aks.cluster_id
}

output "cluster_name" {
  description = "AKS name for controlled onboarding."
  value       = module.aks.cluster_name
}

output "resource_group_name" {
  description = "AKS resource group for controlled onboarding."
  value       = azurerm_resource_group.cluster.name
}

output "fleet_membership_id" {
  description = "Fleet member when enrollment is enabled."
  value       = module.aks.fleet_membership_id
}
