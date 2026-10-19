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
    snet-aks-system-management = { address_prefixes = ["10.1.0.0/24"] }
    snet-aks-user-management   = { address_prefixes = ["10.1.1.0/24"] }
    snet-pe-management         = { address_prefixes = ["10.1.2.0/27"] }
  }
}

aks = {
  name = "aks-management"
  # Complete these three values before planning/applying.
  # Available versions: az aks get-versions --location uksouth -o table
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
      vm_size    = "Standard_D2s_v5"
      node_count = 1
    }
  }
}

# Reference the existing zone without taking ownership of its delegation.
dns = {
  zone = {
    name                = "dev.michaelalinks.com"
    resource_group_name = "kubernetes"
  }
  identity_name = "id-management-dns"
  service_account = {
    namespace = "external-dns"
    name      = "external-dns"
  }
}

argocd = {
  chart_version = "10.10.5"
  values_file   = "argocd-values.yaml"
}
