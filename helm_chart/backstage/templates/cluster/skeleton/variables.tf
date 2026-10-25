variable "cluster" {
  description = "Reviewed workload cluster deployment choices."
  type = object({
    name                    = string
    location                = string
    kubernetes_version      = string
    network_cidr            = string
    system_subnet_cidr      = string
    application_subnet_cidr = string
    admin_group_object_id   = string
    fleet_id                = optional(string, "")
    node_pools = map(object({
      vm_size    = string
      node_count = number
    }))
  })

  validation {
    condition     = can(regex("^aks-[a-z0-9]+(?:-[a-z0-9]+)*$", var.cluster.name)) && length(var.cluster.name) <= 40
    error_message = "Cluster name must be a unique lowercase aks- name of at most 40 characters."
  }

  validation {
    condition     = toset(keys(var.cluster.node_pools)) == toset(["system", "platform", "services"])
    error_message = "Provide exactly one system and two application pools."
  }
}

variable "api_allowlist_vault" {
  description = "Existing platform vault containing the AKS API CIDR JSON array."
  type = object({
    name                = string
    resource_group_name = string
    secret_name         = string
  })
}

variable "registry" {
  description = "Existing registry from which workload pods pull platform images."
  type = object({
    name                = string
    resource_group_name = string
  })
}

variable "management_network" {
  description = "Existing management VNet and private Foundry DNS zones; the new root owns both VNet peerings and its own zone links."
  type = object({
    name                = string
    resource_group_name = string
  })
}
