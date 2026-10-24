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

locals {
  argocd_configmap = yamldecode(file("${path.module}/${var.argocd.configmap_file}"))
  argocd_hostname  = "${var.argocd.dns_label}.${var.dns.zone.name}"

  role_scopes = {
    dns_zone        = data.azurerm_dns_zone.existing.id
    key_vault       = data.azurerm_key_vault.platform.id
    storage_account = data.azurerm_storage_account.platform.id
  }
}

resource "azurerm_resource_group" "this" {
  name     = var.azure.resource_group_name
  location = var.azure.location
  tags     = var.azure.tags
}

resource "azurerm_storage_container" "platform" {
  for_each              = var.platform.storage.containers
  name                  = each.value.name
  storage_account_id    = data.azurerm_storage_account.platform.id
  container_access_type = "private"
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

# Bootstrap owns this cluster-admin group; it needs no Azure RBAC assignments.
module "aks_admin_group" {
  source       = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/entra-group?ref=3e883abf9680cf033fae533a1883d64341bde7ef"
  display_name = var.aks.admin_group.display_name
  description  = var.aks.admin_group.description
  owners       = var.aks.admin_group.owners
  members      = var.aks.admin_group.members
}

module "aks" {
  source                 = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/aks-cluster?ref=8c1370a33f003c81d155027edaac4687aea9b3db"
  name                   = var.aks.name
  location               = var.azure.location
  resource_group_name    = azurerm_resource_group.this.name
  tenant_id              = sensitive(data.azurerm_client_config.current.tenant_id)
  kubernetes_version     = var.aks.kubernetes_version
  admin_group_object_ids = [module.aks_admin_group.object_id]
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

  values = [
    file("${path.module}/${var.argocd.values_file}"),
    yamlencode(merge(
      { extraObjects = [merge(
        local.argocd_configmap,
        { data = merge(local.argocd_configmap.data, { url = "https://${local.argocd_hostname}" }) }
      )] },
      { for component in var.argocd.identity_components : component => {
        podLabels      = { "azure.workload.identity/use" = "true" }
        serviceAccount = { annotations = { "azure.workload.identity/client-id" = module.identities["argocd"].client_id } }
      } }
    ))
  ]

  depends_on = [module.aks]
}
