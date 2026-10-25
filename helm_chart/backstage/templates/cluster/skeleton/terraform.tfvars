cluster = {
  name                    = "${{ values.name }}"
  location                = "${{ values.location }}"
  kubernetes_version      = "${{ values.kubernetesVersion }}"
  network_cidr            = "${{ values.networkCidr }}"
  system_subnet_cidr      = "${{ values.systemSubnetCidr }}"
  application_subnet_cidr = "${{ values.applicationSubnetCidr }}"
  admin_group_object_id   = "${{ values.adminGroupObjectId }}"
  fleet_id                = "${{ values.fleetId }}"
}

api_allowlist_vault = {
  name                = "${{ values.keyVaultName }}"
  resource_group_name = "${{ values.keyVaultResourceGroup }}"
  secret_name         = "${{ values.apiAllowlistSecret }}"
}

registry = {
  name                = "${{ values.registryName }}"
  resource_group_name = "${{ values.registryResourceGroup }}"
}

management_network = {
  name                = "${{ values.managementVnetName }}"
  resource_group_name = "${{ values.managementVnetResourceGroup }}"
}
