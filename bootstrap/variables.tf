variable "container_registries" {
  description = "Existing registries from which the AKS kubelet may pull images."
  type = map(object({
    name                = string
    resource_group_name = string
  }))
}

variable "entra_applications" {
  description = "Terraform-owned bootstrap applications, workload federation and explicit Microsoft Graph consent."
  type = map(object({
    display_name                = string
    group_membership_claims     = optional(set(string), [])
    public_client_redirect_uris = optional(set(string), [])
    graph_permissions           = optional(set(string), [])

    web = optional(object({
      redirect_uris = set(string)
    }))

    federated_credentials = map(object({
      namespace       = string
      service_account = string
    }))
  }))

  validation {
    condition     = alltrue([for key in ["argocd", "crossplane"] : contains(keys(var.entra_applications), key)])
    error_message = "Define the argocd and crossplane bootstrap applications."
  }
}

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
      security_rules = optional(map(object({
        description                = optional(string)
        priority                   = number
        direction                  = string
        access                     = string
        protocol                   = string
        source_port_range          = string
        destination_port_ranges    = list(string)
        source_address_prefix      = string
        destination_address_prefix = string
      })))
    }))
  })
}

variable "private_dns" {
  description = "Private DNS zones owned by bootstrap; optionally link each zone to the management VNet."
  type = map(object({
    name      = string
    vnet_link = optional(bool, false)
  }))
}

variable "foundry" {
  description = "Private management Foundry account and regional model deployment choices."

  type = object({
    name                        = string
    location                    = string
    private_endpoint_subnet_key = string
    private_dns_zone_keys       = set(string)

    projects = map(object({
      display_name = optional(string)
      description  = optional(string)
    }))

    deployments = map(object({
      model_name             = string
      model_version          = string
      capacity               = number
      sku_name               = optional(string, "Standard")
      version_upgrade_option = optional(string, "NoAutoUpgrade")
    }))
  })

}

variable "aks" {
  description = "Management cluster settings and node pools keyed by pool name."

  type = object({
    name               = string
    kubernetes_version = string

    admin_group = object({
      display_name = string
      description  = string
    })

    authorized_ip_ranges_secret = string

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
  description = "Existing public DNS zone."

  type = object({
    zone = object({
      name                = string
      resource_group_name = string
    })
  })
}

variable "identities" {
  description = "Managed identities with their federated service accounts and scoped Azure roles."

  type = map(object({
    name = string

    federated_credentials = map(object({
      namespace       = string
      service_account = string
      audiences       = optional(list(string), ["api://AzureADTokenExchange"])
    }))

    role_assignments = optional(map(object({
      scope                = string
      role_definition_name = string
    })), {})
  }))

  validation {
    condition     = alltrue([for key in ["platform", "postgres", "argocd", "aso"] : contains(keys(var.identities), key)])
    error_message = "Define platform, postgres, argocd and aso bootstrap identities."
  }
}

variable "argocd" {
  description = "Argo CD chart and initial configuration."

  type = object({
    dns_label           = string
    chart_version       = string
    values_file         = string
    configmap_file      = string
    identity_components = optional(set(string), ["controller", "server"])
  })

  validation {
    condition     = contains(var.argocd.identity_components, "server")
    error_message = "Argo SSO requires workload identity on the server component."
  }

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.argocd.dns_label))
    error_message = "Argo CD dns_label must be a single lowercase DNS label (1-63 characters)."
  }
}

variable "platform" {
  description = "Management platform settings and existing Azure services. Terraform owns the disposable data containers."

  type = object({
    cluster_name   = string
    cluster_role   = optional(string, "management")
    git_repository = optional(string, "https://github.com/ai-platform-portfolio/gitops.git")
    git_revision   = optional(string, "main")
    acme_email     = string

    key_vault = object({
      name                = string
      resource_group_name = string
    })

    storage = object({
      account_name        = string
      resource_group_name = string

      containers = map(object({
        name = string
      }))
    })

    sonarqube_sso_display_name = optional(string, "REPLACE_WITH_ENTRA_SAML_APP_NAME")
  })

  validation {
    condition     = contains(["management", "workload"], var.platform.cluster_role)
    error_message = "cluster_role must be management or workload."
  }

  validation {
    condition     = alltrue([for key in ["postgres", "loki_chunks", "loki_ruler", "mimir_blocks", "mimir_ruler", "mimir_alertmanager", "tempo_traces"] : contains(keys(var.platform.storage.containers), key)])
    error_message = "Define the PostgreSQL and observability containers."
  }
}
