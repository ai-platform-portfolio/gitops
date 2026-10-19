variable "azure" {
  description = "Azure location, resource group and tags; account identity comes from the authenticated context."
  type = object({
    location            = string
    resource_group_name = string
    tags                = map(string)
  })
}

variable "network" {
  description = "Management virtual network and named subnet objects."
  type = object({
    name          = string
    address_space = list(string)
    subnets = map(object({
      address_prefixes            = list(string)
      service_endpoints           = optional(list(string), [])
      network_security_group_name = optional(string)
    }))
  })
}

variable "aks" {
  description = "Management cluster settings and node pools keyed by pool name."
  type = object({
    name                   = string
    kubernetes_version     = string
    admin_group_object_ids = list(string)
    authorized_ip_ranges   = list(string)
    system_node_pool = object({
      subnet_key      = string
      vm_size         = string
      node_count      = number
      os_disk_size_gb = optional(number, 64)
    })
    user_node_pools = map(object({
      subnet_key      = string
      vm_size         = string
      node_count      = number
      os_disk_size_gb = optional(number, 64)
    }))
  })
}

variable "dns" {
  description = "Existing public DNS zone, managed identity and ExternalDNS service account."
  type = object({
    zone = object({
      name                = string
      resource_group_name = string
    })
    identity_name = string
    service_account = object({
      namespace = string
      name      = string
    })
  })
}

variable "argocd" {
  description = "ArgoCD Helm chart and initial values file."
  type = object({
    chart_version = string
    values_file   = string
  })
}
