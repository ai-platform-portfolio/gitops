# Complete the REPLACE values before use. Account identity comes from the Azure CLI session.
azure = {
  location            = "uksouth"
  resource_group_name = "rg-management"
  tags = {
    purpose    = "management"
    managed-by = "terraform"
  }
}

network = {
  name          = "vnet-management"
  address_space = ["10.1.0.0/16"]
  subnets = {
    snet-aks-system-management = {
      address_prefixes = ["10.1.0.0/24"]
    }

    snet-aks-user-management = {
      address_prefixes = ["10.1.1.0/24"]
    }

    snet-pe-management = {
      address_prefixes = ["10.1.2.0/27"]
    }
  }
}

aks = {
  name = "aks-management"
  # Set actual values in ignored bootstrap/local.tfvars. The Makefile passes that
  # file with -var-file when present; override it with TFVARS=/path/to/settings.tfvars.
  kubernetes_version     = "REPLACE_WITH_SUPPORTED_VERSION"
  admin_group_object_ids = ["REPLACE_WITH_ENTRA_SECURITY_GROUP_ID"]
  authorized_ip_ranges   = ["REPLACE_WITH_YOUR_PUBLIC_IP/32"]
  system_node_pool = {
    subnet_key = "snet-aks-system-management"
    vm_size    = "Standard_D4s_v5"
    node_count = 1
  }

  user_node_pools = {
    platform = {
      subnet_key = "snet-aks-user-management"
      vm_size    = "Standard_D8s_v5"
      node_count = 3
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
  chart_version  = "10.10.5"
  values_file    = "argocd-values.yaml"
  configmap_file = "argocd-configmap.yaml"
}

platform = {
  cluster_name               = "management"
  cluster_role               = "management"
  sonarqube_sso_display_name = "REPLACE_WITH_ENTRA_SAML_APP_NAME"
  acme_email                 = "REPLACE_WITH_ACME_EMAIL"
  key_vault = {
    name                = "REPLACE_WITH_KEY_VAULT_NAME"
    resource_group_name = "kubernetes"
  }

  storage = {
    account_name        = "REPLACE_WITH_PLATFORM_STORAGE_ACCOUNT"
    resource_group_name = "kubernetes"
    containers = {
      metadata = {
        name = "platform-metadata"
      }

      postgres = {
        name = "postgres-backups"
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

    legacy_metadata = {
      container = "postgres-backups"
    }
  }
}

identities = {
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

      loki_chunks = {
        scope                = "storage:loki_chunks"
        role_definition_name = "Storage Blob Data Contributor"
      }

      loki_ruler = {
        scope                = "storage:loki_ruler"
        role_definition_name = "Storage Blob Data Contributor"
      }

      mimir_blocks = {
        scope                = "storage:mimir_blocks"
        role_definition_name = "Storage Blob Data Contributor"
      }

      mimir_ruler = {
        scope                = "storage:mimir_ruler"
        role_definition_name = "Storage Blob Data Contributor"
      }

      mimir_alertmanager = {
        scope                = "storage:mimir_alertmanager"
        role_definition_name = "Storage Blob Data Contributor"
      }

      tempo_traces = {
        scope                = "storage:tempo_traces"
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
        scope                = "storage:postgres"
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

    role_assignments = {
    }
  }
}
