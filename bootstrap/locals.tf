locals {
  role_scopes = merge({
    dns_zone  = data.azurerm_dns_zone.existing.id
    key_vault = data.azurerm_key_vault.platform.id
    }, {
    for key, container in var.platform.storage.containers : "storage:${key}" => "${data.azurerm_storage_account.platform.id}/blobServices/default/containers/${container.name}"
  })
}
