data "azurerm_client_config" "current" {}

data "azurerm_dns_zone" "existing" {
  name                = var.dns.zone.name
  resource_group_name = var.dns.zone.resource_group_name
}

resource "azurerm_resource_group" "this" {
  name     = var.azure.resource_group_name
  location = var.azure.location
  tags     = var.azure.tags
}

module "network" {
  source              = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/network?ref=8c1370a33f003c81d155027edaac4687aea9b3db"
  name                = var.network.name
  resource_group_name = azurerm_resource_group.this.name
  location            = var.azure.location
  address_space       = var.network.address_space
  subnets             = var.network.subnets
  tags                = var.azure.tags
}

module "aks" {
  source                 = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/aks-cluster?ref=8c1370a33f003c81d155027edaac4687aea9b3db"
  name                   = var.aks.name
  location               = var.azure.location
  resource_group_name    = azurerm_resource_group.this.name
  tenant_id              = sensitive(data.azurerm_client_config.current.tenant_id)
  kubernetes_version     = var.aks.kubernetes_version
  admin_group_object_ids = var.aks.admin_group_object_ids
  authorized_ip_ranges   = var.aks.authorized_ip_ranges
  vnet_subnet_id         = module.network.subnet_ids[var.aks.system_node_pool.subnet_key]
  system_node_pool = {
    vm_size         = var.aks.system_node_pool.vm_size
    node_count      = var.aks.system_node_pool.node_count
    os_disk_size_gb = var.aks.system_node_pool.os_disk_size_gb
  }
  user_node_pools = {
    for name, pool in var.aks.user_node_pools : name => {
      vm_size         = pool.vm_size
      node_count      = pool.node_count
      os_disk_size_gb = pool.os_disk_size_gb
      vnet_subnet_id  = module.network.subnet_ids[pool.subnet_key]
    }
  }
  tags = var.azure.tags
}


module "dns_identity" {
  source              = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/managed-identity?ref=8c1370a33f003c81d155027edaac4687aea9b3db"
  name                = var.dns.identity_name
  location            = var.azure.location
  resource_group_name = azurerm_resource_group.this.name
  federated_credentials = {
    external-dns = {
      issuer  = module.aks.oidc_issuer_url
      subject = "system:serviceaccount:${var.dns.service_account.namespace}:${var.dns.service_account.name}"
    }
  }
  tags = var.azure.tags
}

resource "azurerm_role_assignment" "dns" {
  scope                = data.azurerm_dns_zone.existing.id
  role_definition_name = "DNS Zone Contributor"
  principal_id         = module.dns_identity.principal_id
  principal_type       = "ServicePrincipal"
}

module "argocd" {
  source        = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/argocd-bootstrap?ref=8c1370a33f003c81d155027edaac4687aea9b3db"
  chart_version = var.argocd.chart_version
  values        = [file("${path.module}/${var.argocd.values_file}")]
  depends_on    = [module.aks]
}
