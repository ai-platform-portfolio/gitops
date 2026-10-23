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
    condition     = alltrue([for key in ["platform", "postgres", "argocd"] : contains(keys(var.identities), key)])
    error_message = "Define platform, postgres and argocd identities."
  }
}

variable "argocd" {
  description = "ArgoCD Helm chart and initial values file."

  type = object({
    chart_version       = string
    values_file         = string
    configmap_file      = optional(string, "argocd-configmap.yaml")
    identity_components = optional(set(string), ["controller", "server"])
  })
}

variable "platform" {
  description = "Management platform settings and existing Azure services. Terraform owns the disposable data containers."

  type = object({
    cluster_name   = string
    domain_name    = optional(string)
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

    # Enabling Atlantis requires a bootstrap identities.aso provisioner entry. GitHub
    # secrets are read by External Secrets; only non-secret settings go to Argo.
    atlantis = optional(object({
      enabled         = optional(bool, false)
      github_app_id   = optional(string, "")
      github_app_slug = optional(string, "")
      github_team     = optional(string, "")
      repositories    = optional(set(string), [])

      azure = optional(object({
        identity_resource_group_name = optional(string, "")

        role_assignments = optional(map(object({
          resource_group_name  = string
          storage_account_name = optional(string)
          role_definition_name = string
        })), {})
      }), {})
    }), {})

    sonarqube_sso_display_name = optional(string, "REPLACE_WITH_ENTRA_SAML_APP_NAME")
  })

  validation {
    condition = !var.platform.atlantis.enabled || (
      var.platform.cluster_role == "management" &&
      can(regex("^[0-9]+$", var.platform.atlantis.github_app_id)) &&
      can(regex("^[a-zA-Z0-9-]+$", var.platform.atlantis.github_app_slug)) &&
      can(regex("^[a-zA-Z0-9-]+$", var.platform.atlantis.github_team)) &&
      length(var.platform.atlantis.azure.identity_resource_group_name) > 0 &&
      length(var.platform.atlantis.azure.role_assignments) > 0 &&
      alltrue([for name, role in var.platform.atlantis.azure.role_assignments :
        can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", name)) && length(name) <= 54 &&
        length(role.resource_group_name) > 0 && length(role.role_definition_name) > 0 &&
        (role.storage_account_name == null || can(regex("^[a-z0-9]{3,24}$", role.storage_account_name)))
      ]) &&
      anytrue([for repo in var.platform.atlantis.repositories : !startswith(repo, "!")]) &&
      alltrue([for repo in var.platform.atlantis.repositories : can(regex("^!?github\\.com/[a-zA-Z0-9-]+/([a-zA-Z0-9_.-]+|\\*)$", repo))])
    )
    error_message = "Atlantis requires management role, numeric GitHub App ID, App/team slugs and an Azure identity resource group and scoped roles, github.com/owner/repository or github.com/owner/* patterns; ! exclusions are allowed, with at least one positive pattern."
  }

  validation {
    condition     = contains(["management", "workload"], var.platform.cluster_role)
    error_message = "cluster_role must be management or workload."
  }

  validation {
    condition     = alltrue([for key in ["postgres", "loki_chunks", "loki_ruler", "mimir_blocks", "mimir_ruler", "mimir_alertmanager", "tempo_traces"] : contains(keys(var.platform.storage.containers), key)])
    error_message = "Define the PostgreSQL and observability containers."
  }
}
