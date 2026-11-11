data "azurerm_client_config" "current" {}

data "azurerm_container_registry" "existing" {
  for_each            = var.container_registries
  name                = each.value.name
  resource_group_name = each.value.resource_group_name
}

# Operator identity persists while disposable clusters are rebuilt.
data "azuread_group" "aks_admin" {
  display_name = var.aks.admin_group.display_name
}

# Reuse the existing GitHub CI publisher; its registration and federation remain external.
data "azuread_service_principal" "ci_publisher" {
  display_name = var.ci_publisher.service_principal_display_name
}

data "azuread_service_principal" "microsoft_graph" {
  client_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph
}

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

# The provider marks the secret value sensitive, redacting derived ranges in plans.
# The value is still stored in the encrypted Terraform backend.
data "azurerm_key_vault_secret" "aks_api_ranges" {
  name         = var.aks.authorized_ip_ranges_secret
  key_vault_id = data.azurerm_key_vault.platform.id
}

locals {
  argocd_hostname = "${var.argocd.dns_label}.${var.dns.zone.name}"
  foundry_name    = "${var.foundry.name_prefix}-${terraform_data.foundry_spinup_date.output}-${random_integer.foundry_index.result}"

  # The ConfigMap remains readable YAML; Terraform fills only deployment context.
  argocd_configmap = yamldecode(templatefile("${path.module}/${var.argocd.configmap_file}", {
    argocd_url    = "https://${local.argocd_hostname}"
    sso_issuer    = sensitive("https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0")
    sso_client_id = module.entra_applications["argocd"].client_id
  }))

  role_scopes = merge({
    subscription    = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
    resource_group  = azurerm_resource_group.this.id
    dns_zone        = data.azurerm_dns_zone.existing.id
    key_vault       = data.azurerm_key_vault.platform.id
    storage_account = data.azurerm_storage_account.platform.id
  }, { for name, registry in data.azurerm_container_registry.existing : "registry_${name}" => registry.id })
}

# Reusing a deleted Foundry subdomain produced a hosted-runtime 404 even after
# ARM and agent metadata succeeded. A full state destroy yields a fresh name;
# retries and partial applies keep the same date and random index.
resource "terraform_data" "foundry_spinup_date" {
  input = var.foundry_spinup_date

  lifecycle {
    ignore_changes = [input]
  }
}

resource "random_integer" "foundry_index" {
  min = 10000000
  max = 99999999
}

resource "azurerm_key_vault_secret" "foundry_name" {
  name         = "foundry-management-account-name"
  value        = local.foundry_name
  key_vault_id = data.azurerm_key_vault.platform.id
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

resource "azurerm_private_dns_zone" "private" {
  for_each            = var.private_dns
  name                = each.value.name
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.azure.tags
}

# Keep each network one module instance; the caller owns repetition and regions.
module "network" {
  for_each = {
    management    = merge(var.network, { location = var.azure.location })
    model_gateway = merge(var.model_gateway.network, { location = var.model_gateway.location })
    foundry       = merge(var.foundry.network, { location = var.foundry.location })
  }

  source              = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/network?ref=f4510bed40f1191a5d4d34adfaab1e4dc9ab71ac"
  name                = each.value.name
  resource_group_name = azurerm_resource_group.this.name
  location            = each.value.location
  address_space       = each.value.address_space
  subnets             = each.value.subnets
  tags                = var.azure.tags

  private_dns_zone_links = {
    for key, zone in var.private_dns : key => {
      name                  = each.value.name
      resource_group_name   = azurerm_resource_group.this.name
      private_dns_zone_name = azurerm_private_dns_zone.private[key].name
    } if zone.vnet_link && (each.key == "management" || (each.key == "foundry" && contains(var.foundry.private_dns_zone_keys, key)) || (each.key == "model_gateway" && key == var.model_gateway.private_dns_zone_key))
  }
}

resource "azurerm_virtual_network_peering" "management_to_foundry" {
  name                      = "management-to-foundry"
  resource_group_name       = azurerm_resource_group.this.name
  virtual_network_name      = var.network.name
  remote_virtual_network_id = module.network["foundry"].vnet_id

  # VNet IDs exist before their subnet and DNS-link writes have completed.
  depends_on = [module.network["management"], module.network["foundry"]]
}

resource "azurerm_virtual_network_peering" "foundry_to_management" {
  name                      = "foundry-to-management"
  resource_group_name       = azurerm_resource_group.this.name
  virtual_network_name      = var.foundry.network.name
  remote_virtual_network_id = module.network["management"].vnet_id

  # Azure peering operations must run sequentially, including during teardown.
  depends_on = [azurerm_virtual_network_peering.management_to_foundry]
}

# Apply this ownership transfer before teardown; retain the existing Entra object.
removed {
  from = module.aks_admin_group.azuread_group.this

  lifecycle {
    destroy = false
  }
}

module "aks" {
  source                 = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/aks-cluster?ref=d2c8a5391b4935fda9cbde79a828b905a8f500b6"
  name                   = var.aks.name
  location               = var.azure.location
  resource_group_name    = azurerm_resource_group.this.name
  tenant_id              = sensitive(data.azurerm_client_config.current.tenant_id)
  kubernetes_version     = var.aks.kubernetes_version
  sku_tier               = var.aks.sku_tier
  admin_group_object_ids = [data.azuread_group.aks_admin.object_id]
  authorized_ip_ranges   = jsondecode(data.azurerm_key_vault_secret.aks_api_ranges.value)
  container_registries   = { for name, registry in data.azurerm_container_registry.existing : name => registry.id }
  vnet_subnet_id         = module.network["management"].subnet_ids[var.aks.system_node_pool.subnet_key]

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
      vnet_subnet_id  = module.network["management"].subnet_ids[pool.subnet_key]
    }
  }

  tags = var.azure.tags
}

# Lets GitHub Actions publish and manage hosted Foundry agent versions across
# the subscription, independently of AKS. Only this new grant is Terraform-owned;
# the existing SPN, GitHub federation and other grants are not adopted here.
resource "azurerm_role_assignment" "ci_publisher_foundry" {
  scope                = local.role_scopes["subscription"]
  role_definition_name = var.ci_publisher.foundry_role_definition_name
  principal_id         = data.azuread_service_principal.ci_publisher.object_id
  principal_type       = "ServicePrincipal"
}

module "identities" {
  for_each            = { for key, identity in var.identities : key => identity if key != "aso" }
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

# ASO needs a role on Foundry, which depends on the separate Foundry identity.
# Keep that dependency outside the shared identity loop to avoid a cycle.
module "aso_identity" {
  source              = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/managed-identity?ref=453269ded9ea65cbc40f5ce9a240f022f8bea5bf"
  name                = var.identities["aso"].name
  location            = var.azure.location
  resource_group_name = azurerm_resource_group.this.name

  federated_credentials = {
    for name, credential in var.identities["aso"].federated_credentials : name => {
      issuer    = module.aks.oidc_issuer_url
      subject   = "system:serviceaccount:${credential.namespace}:${credential.service_account}"
      audiences = credential.audiences
    }
  }

  role_assignments = {
    for name, assignment in var.identities["aso"].role_assignments : name => {
      scope                = assignment.scope == "foundry_account" ? module.foundry.id : local.role_scopes[assignment.scope]
      role_definition_name = assignment.role_definition_name
    }
  }

  tags = var.azure.tags
}

# Bootstrap grants the operator group Foundry access; application consumers
# retain separate ownership of their runtime identities and role assignments.
module "foundry" {
  source                     = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/foundry?ref=b87f2b35381b36b6951bf9a067e22d8ab6a8ee40"
  name                       = local.foundry_name
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.foundry.location
  managed_identity_id        = module.identities["foundry"].identity_id
  private_endpoint_location  = var.foundry.location
  private_endpoint_subnet_id = module.network["foundry"].subnet_ids[var.foundry.private_endpoint_subnet_key]
  agent_subnet_id            = module.network["foundry"].subnet_ids[var.foundry.agent_subnet_key]
  private_dns_zone_ids       = [for key in var.foundry.private_dns_zone_keys : azurerm_private_dns_zone.private[key].id]
  projects                   = var.foundry.projects
  deployments                = var.foundry.deployments
  allowed_deployment_skus    = ["DataZoneStandard"]
  tags                       = var.azure.tags

  account_settings = {
    public_network_access_enabled = var.foundry.network_access.public_network_access_enabled
  }

  network_acls = { default_action = var.foundry.network_access.default_action }

  role_assignments = {
    aks_admins = {
      principal_id         = data.azuread_group.aks_admin.object_id
      role_definition_name = "Foundry Owner"
    }

    project_identity = {
      principal_id         = module.identities["foundry"].principal_id
      role_definition_name = "Foundry User"
    }
  }

  depends_on = [
    azurerm_key_vault_secret.foundry_name,
    module.network["foundry"],
    azurerm_virtual_network_peering.management_to_foundry,
    azurerm_virtual_network_peering.foundry_to_management,
  ]
}

# One managed server hosts the staging and production databases. The chart and
# ASO own their per-environment databases, identities and migration lifecycle.
module "postgres" {
  source                     = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/postgresql-flexible-server?ref=1985492e097602a3f4a4367f5802df4972004082"
  name                       = var.postgres.name
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.azure.location
  zone                       = var.postgres.zone
  tenant_id                  = sensitive(data.azurerm_client_config.current.tenant_id)
  postgres_version           = var.postgres.version
  sku_name                   = var.postgres.sku_name
  storage_mb                 = var.postgres.storage_mb
  backup_retention_days      = var.postgres.backup_retention_days
  private_endpoint_subnet_id = module.network["management"].subnet_ids[var.postgres.private_endpoint_subnet_key]
  private_dns_zone_id        = azurerm_private_dns_zone.private["postgres"].id

  entra_administrators = {
    aks_admins = {
      object_id      = data.azuread_group.aks_admin.object_id
      principal_name = var.aks.admin_group.display_name
      principal_type = "Group"
    }
  }

  tags = var.azure.tags

  depends_on = [module.network["management"]]
}

# Bootstrap applications share one library call; their choices live in tfvars.
module "entra_applications" {
  for_each     = var.entra_applications
  source       = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/entra-application?ref=ebc4eab1a8b3f630ac11079939e96ed32446aa1d"
  display_name = each.value.display_name
  owners       = [data.azurerm_client_config.current.object_id]

  group_membership_claims     = each.value.group_membership_claims
  public_client_redirect_uris = each.value.public_client_redirect_uris
  web                         = each.value.web

  federated_credentials = { for name, credential in each.value.federated_credentials : name => {
    issuer  = module.aks.oidc_issuer_url
    subject = "system:serviceaccount:${credential.namespace}:${credential.service_account}"
  } }

  required_resource_access = length(each.value.graph_permissions) == 0 ? {} : {
    graph = {
      resource_app_id = data.azuread_service_principal.microsoft_graph.client_id
      permissions = { for name in each.value.graph_permissions : name => {
        id   = data.azuread_service_principal.microsoft_graph.app_role_ids[name]
        type = "Role"
      } }
    }
  }

  application_permission_grants = { for name in each.value.graph_permissions : name => {
    resource_principal_id = data.azuread_service_principal.microsoft_graph.object_id
    app_role_id           = data.azuread_service_principal.microsoft_graph.app_role_ids[name]
  } }
}

# Azure can report AKS and its API allowlist update as complete before clients
# can reliably use the endpoint. Gate Helm on both API health and authorisation.
resource "terraform_data" "aks_api_ready" {
  triggers_replace = [
    module.aks.cluster_id,
    data.azuread_group.aks_admin.object_id,
    data.azurerm_key_vault_secret.aks_api_ranges.version,
    filesha256("${path.module}/scripts/wait_for_aks.py"),
  ]

  provisioner "local-exec" {
    command     = "python3 scripts/wait_for_aks.py"
    working_dir = path.module

    environment = {
      AKS_NAME           = module.aks.cluster_name
      AKS_RESOURCE_GROUP = azurerm_resource_group.this.name
    }
  }

  depends_on = [module.aks]
}

module "argocd" {
  source        = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/argocd-bootstrap?ref=8c1370a33f003c81d155027edaac4687aea9b3db"
  chart_version = var.argocd.chart_version

  values = [
    templatefile("${path.module}/${var.argocd.values_file}", { admin_group_id = data.azuread_group.aks_admin.object_id }),
    yamlencode(merge(
      { extraObjects = [local.argocd_configmap] },
      { for component in var.argocd.identity_components : component => {
        podLabels      = { "azure.workload.identity/use" = "true" }
        serviceAccount = { annotations = { "azure.workload.identity/client-id" = module.identities["argocd"].client_id } }
      } }
    ))
  ]

  depends_on = [terraform_data.aks_api_ready, module.entra_applications]
}

# Foundation only: no APIs, inference routes or GCP runtime trust are activated.
module "model_gateway" {
  source                     = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/apim?ref=f4510bed40f1191a5d4d34adfaab1e4dc9ab71ac"
  name                       = var.model_gateway.name
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.model_gateway.location
  sku_name                   = var.model_gateway.sku_name
  publisher_name             = var.model_gateway.publisher.name
  publisher_email            = var.model_gateway.publisher.email
  managed_identity_id        = module.identities["model_gateway"].identity_id
  integration_subnet_id      = module.network["model_gateway"].subnet_ids[var.model_gateway.integration_subnet_key]
  private_endpoint_subnet_id = module.network["model_gateway"].subnet_ids[var.model_gateway.private_endpoint_subnet_key]
  private_dns_zone_id        = azurerm_private_dns_zone.private[var.model_gateway.private_dns_zone_key].id
  tags                       = var.azure.tags
}
