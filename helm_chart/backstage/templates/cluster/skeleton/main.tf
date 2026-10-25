data "azurerm_client_config" "current" {}

data "azurerm_key_vault" "platform" {
  name                = var.api_allowlist_vault.name
  resource_group_name = var.api_allowlist_vault.resource_group_name
}

# CIDRs remain in Key Vault. They are redacted in normal plans but retained in
# encrypted Terraform state by the provider; do not export raw plan JSON.
data "azurerm_key_vault_secret" "api_ranges" {
  name         = var.api_allowlist_vault.secret_name
  key_vault_id = data.azurerm_key_vault.platform.id
}

data "azurerm_container_registry" "platform" {
  name                = var.registry.name
  resource_group_name = var.registry.resource_group_name
}

data "azurerm_virtual_network" "management" {
  name                = var.management_network.name
  resource_group_name = var.management_network.resource_group_name
}

data "azurerm_private_dns_zone" "foundry" {
  for_each = toset([
    "privatelink.openai.azure.com",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.services.ai.azure.com",
  ])

  name                = each.key
  resource_group_name = var.management_network.resource_group_name
}

resource "azurerm_resource_group" "cluster" {
  name     = "rg-${var.cluster.name}"
  location = var.cluster.location
  tags     = { workload_cluster = var.cluster.name }
}

module "network" {
  source              = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/network?ref=6477ffb7fde449bc84b8b0997e89df3889709caf"
  name                = "vnet-${var.cluster.name}"
  location            = azurerm_resource_group.cluster.location
  resource_group_name = azurerm_resource_group.cluster.name
  address_space       = [var.cluster.network_cidr]
  subnets = {
    system   = { address_prefixes = [var.cluster.system_subnet_cidr] }
    platform = { address_prefixes = [var.cluster.application_subnet_cidr] }
  }
  private_dns_zone_links = {
    for zone_name in keys(data.azurerm_private_dns_zone.foundry) : zone_name => {
      name                  = "${var.cluster.name}-${replace(zone_name, ".", "-")}"
      resource_group_name   = var.management_network.resource_group_name
      private_dns_zone_name = zone_name
    }
  }
  tags = azurerm_resource_group.cluster.tags
}

resource "azurerm_virtual_network_peering" "to_management" {
  name                      = "${var.cluster.name}-to-management"
  resource_group_name       = azurerm_resource_group.cluster.name
  virtual_network_name      = "vnet-${var.cluster.name}"
  remote_virtual_network_id = data.azurerm_virtual_network.management.id

  depends_on = [module.network]
}

resource "azurerm_virtual_network_peering" "from_management" {
  name                      = "management-to-${var.cluster.name}"
  resource_group_name       = var.management_network.resource_group_name
  virtual_network_name      = data.azurerm_virtual_network.management.name
  remote_virtual_network_id = module.network.vnet_id
}

module "aks" {
  source                 = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/aks-cluster?ref=5bbbcbf8cd2110e1e023046ade0b2e62c635b006"
  name                   = var.cluster.name
  location               = azurerm_resource_group.cluster.location
  resource_group_name    = azurerm_resource_group.cluster.name
  tenant_id              = sensitive(data.azurerm_client_config.current.tenant_id)
  kubernetes_version     = var.cluster.kubernetes_version
  admin_group_object_ids = [var.cluster.admin_group_object_id]
  azure_rbac_enabled     = true
  authorized_ip_ranges   = jsondecode(data.azurerm_key_vault_secret.api_ranges.value)
  container_registries   = { platform = data.azurerm_container_registry.platform.id }
  vnet_subnet_id         = module.network.subnet_ids["system"]

  system_node_pool = {
    vm_size    = "Standard_D4as_v4"
    node_count = 1
  }

  user_node_pools = {
    platform = {
      vm_size        = "Standard_D4as_v4"
      node_count     = 2
      vnet_subnet_id = module.network.subnet_ids["platform"]
    }
  }

  fleet_membership = var.cluster.fleet_id == "" ? null : {
    fleet_id = var.cluster.fleet_id
    name     = var.cluster.name
    group    = "workload"
  }

  tags = azurerm_resource_group.cluster.tags
}

