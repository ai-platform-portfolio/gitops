# Deployment configuration: environment metadata belongs here, credentials do not.
# The API allowlist comes from Key Vault; authentication comes from the caller.
container_registries = {
  platform = {
    name                = "localacrk8s"
    resource_group_name = "kubernetes"
  }
}

entra_applications = {
  argocd = {
    display_name                = "portfolio-argocd"
    group_membership_claims     = ["SecurityGroup"]
    public_client_redirect_uris = ["http://localhost:8085/auth/callback"]

    web = {
      # Keep this callback aligned with argocd.dns_label and dns.zone.name below.
      redirect_uris = ["https://argocd-management.dev.michaelalinks.com/auth/callback"]
    }

    federated_credentials = {
      argocd-server = {
        namespace       = "argocd"
        service_account = "argocd-server"
      }
    }
  }

  crossplane = {
    display_name = "portfolio-management-crossplane"

    federated_credentials = {
      azuread = {
        namespace       = "crossplane-system"
        service_account = "provider-azuread"
      }
    }

    # Application ownership restricts writes; assignments require tenant-wide consent.
    graph_permissions = [
      "Application.ReadWrite.OwnedBy",
      "Application.Read.All",
      "AppRoleAssignment.ReadWrite.All",
    ]
  }
}

azure = {
  location            = "uksouth"
  resource_group_name = "rg-management"

  tags = {
    purpose    = "management"
    managed-by = "terraform"
  }
}

network = {
  name = "vnet-management"

  address_space = ["10.1.0.0/16"]

  subnets = {
    snet-aks-system-management = {
      address_prefixes = ["10.1.0.0/24"]

      # AKS also filters traffic through its managed NIC NSG.
      security_rules = {
        public-web = {
          description                = "Allow public HTTP/S load balancer traffic"
          priority                   = 100
          direction                  = "Inbound"
          access                     = "Allow"
          protocol                   = "Tcp"
          source_port_range          = "*"
          destination_port_ranges    = ["80", "443"]
          source_address_prefix      = "Internet"
          destination_address_prefix = "*"
        }
      }
    }
    snet-aks-user-management = {
      address_prefixes = ["10.1.1.0/24"]

      # AKS also filters traffic through its managed NIC NSG.
      security_rules = {
        public-web = {
          description                = "Allow public HTTP/S load balancer traffic"
          priority                   = 100
          direction                  = "Inbound"
          access                     = "Allow"
          protocol                   = "Tcp"
          source_port_range          = "*"
          destination_port_ranges    = ["80", "443"]
          source_address_prefix      = "Internet"
          destination_address_prefix = "*"
        }
      }
    }
    snet-pe-management = {
      address_prefixes = ["10.1.2.0/27"]
    }
  }
}

private_dns = {
  zones = {
    openai            = { name = "privatelink.openai.azure.com" }
    cognitiveservices = { name = "privatelink.cognitiveservices.azure.com" }
    services_ai       = { name = "privatelink.services.ai.azure.com" }
  }

  vnet_links = {
    openai            = { name = "management", zone_key = "openai" }
    cognitiveservices = { name = "management", zone_key = "cognitiveservices" }
    services_ai       = { name = "management", zone_key = "services_ai" }
  }
}

foundry = {
  name                        = "foundry-management-portfolio"
  location                    = "uksouth"
  private_endpoint_subnet_key = "snet-pe-management"
  private_dns_zone_keys       = ["openai", "cognitiveservices", "services_ai"]

  projects = {
    management = {
      display_name = "Management"
      description  = "Private management platform inference"
    }
  }

  deployments = {
    holmes = {
      model_name    = "gpt-4.1-mini"
      model_version = "2025-04-14"
      capacity      = 10
    }
  }
}

aks = {
  name               = "aks-management"
  kubernetes_version = "1.36.5"

  admin_group = {
    display_name = "aks-platform-admins"
    description  = "Administrators of the management AKS cluster."
  }

  authorized_ip_ranges_secret = "aks-api-authorized-ip-ranges"

  system_node_pool = {
    subnet_key = "snet-aks-system-management"
    vm_size    = "Standard_D4as_v4"
    node_count = 1
  }

  user_node_pools = {
    platform = {
      subnet_key = "snet-aks-user-management"
      # Each node supports eight Azure disks; the full chart exceeds one node's limit.
      vm_size    = "Standard_D4as_v4"
      node_count = 2
    }
  }
}

dns = {
  zone = {
    name                = "dev.michaelalinks.com"
    resource_group_name = "kubernetes"
  }
}

argocd = {
  # Use a fresh label; existing Azure DNS records are not adopted or overwritten.
  dns_label      = "argocd-management"
  chart_version  = "10.10.5"
  values_file    = "argocd-values.yaml"
  configmap_file = "argocd-configmap.yaml"
}

platform = {
  cluster_name               = "management"
  cluster_role               = "management"
  sonarqube_sso_display_name = "portfolio-sonarqube"
  acme_email                 = "michaelalinks@gmail.com"

  key_vault = {
    name                = "localdevenv"
    resource_group_name = "kubernetes"
  }

  storage = {
    account_name        = "mlinkslgtmdevpg"
    resource_group_name = "kubernetes"

    containers = {
      postgres = {
        name = "management-postgres-backups"
      }
      loki_chunks = {
        name = "management-loki-chunks"
      }
      loki_ruler = {
        name = "management-loki-ruler"
      }
      mimir_blocks = {
        name = "management-mimir-blocks"
      }
      mimir_ruler = {
        name = "management-mimir-ruler"
      }
      mimir_alertmanager = {
        name = "management-mimir-alertmanager"
      }
      tempo_traces = {
        name = "management-tempo-traces"
      }
    }
  }
}

identities = {
  foundry = {
    name                  = "id-management-foundry"
    federated_credentials = {}
  }

  platform = {
    name = "id-management-platform"

    federated_credentials = {
      cert-manager = {
        namespace       = "cert-manager"
        service_account = "cert-manager"
      }
      external-dns = {
        namespace       = "external-dns"
        service_account = "external-dns"
      }
      external-secrets = {
        namespace       = "external-secrets"
        service_account = "eso-akv-wi"
      }
      lgtm = {
        namespace       = "lgtm"
        service_account = "lgtm"
      }
    }

    role_assignments = {
      dns = {
        scope                = "dns_zone"
        role_definition_name = "DNS Zone Contributor"
      }
      secrets = {
        scope                = "key_vault"
        role_definition_name = "Key Vault Secrets User"
      }
      storage = {
        scope                = "storage_account"
        role_definition_name = "Storage Blob Data Contributor"
      }
    }
  }

  postgres = {
    name = "id-management-postgres"

    federated_credentials = {
      postgres = {
        namespace       = "postgres"
        service_account = "platform"
      }
    }

    role_assignments = {
      backups = {
        scope                = "storage_account"
        role_definition_name = "Storage Blob Data Contributor"
      }
    }
  }

  argocd = {
    name = "id-management-argocd"

    federated_credentials = {
      argocd-application-controller = {
        namespace       = "argocd"
        service_account = "argocd-application-controller"
      }
      argocd-server = {
        namespace       = "argocd"
        service_account = "argocd-server"
      }
    }
  }
}
