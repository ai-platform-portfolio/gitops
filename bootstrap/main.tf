data "azurerm_client_config" "current" {}

data "azurerm_dns_zone" "existing" {
  name                = var.dns.zone.name
  resource_group_name = var.dns.zone.resource_group_name
}

data "azurerm_storage_account" "platform" {
  name                = var.platform.storage.account_name
  resource_group_name = var.platform.storage.resource_group_name
}

data "azurerm_key_vault" "platform" {
  name                = var.platform.key_vault.name
  resource_group_name = var.platform.key_vault.resource_group_name
}

resource "azurerm_resource_group" "this" {
  lifecycle {
    precondition {
      condition     = var.platform.cluster_name == (terraform.workspace == "default" ? "management" : terraform.workspace)
      error_message = "Select the workspace matching platform.cluster_name; default is reserved for management."
    }
  }

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

module "identities" {
  for_each            = var.identities
  source              = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/managed-identity?ref=453269ded9ea65cbc40f5ce9a240f022f8bea5bf"
  name                = each.value.name
  location            = var.azure.location
  resource_group_name = azurerm_resource_group.this.name
  federated_credentials = {
    for name, credential in each.value.federated_credentials : name => {
      issuer    = module.aks.oidc_issuer_url
      subject   = "system:serviceaccount:${credential.namespace}:${credential.service_account}"
      audiences = credential.audiences
    }
  }

  role_assignments = {
    for name, assignment in each.value.role_assignments : name => {
      scope                = local.role_scopes[assignment.scope]
      role_definition_name = assignment.role_definition_name
    }
  }

  tags = var.azure.tags
}

module "argocd" {
  source        = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/argocd-bootstrap?ref=8c1370a33f003c81d155027edaac4687aea9b3db"
  chart_version = var.argocd.chart_version
  values = [file("${path.module}/${var.argocd.values_file}"), yamlencode(merge({
    extraObjects = [yamldecode(file("${path.module}/${var.argocd.configmap_file}"))]
    }, {
    for component in var.argocd.identity_components : component => {
      podLabels      = { "azure.workload.identity/use" = "true" }
      serviceAccount = { annotations = { "azure.workload.identity/client-id" = module.identities["argocd"].client_id } }
    }

  }))]
  depends_on = [module.aks]
}
