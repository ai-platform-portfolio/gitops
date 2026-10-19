terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
    helm    = { source = "hashicorp/helm", version = "~> 2.0" }
  }
}

provider "azurerm" {
  features {}
  use_cli = true
}

provider "helm" {
  kubernetes {
    host                   = module.aks.host
    cluster_ca_certificate = module.aks.cluster_ca_certificate
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "kubelogin"
      # Microsoft's public AKS server application ID; shared across environments.
      # https://azure.github.io/kubelogin/concepts/aks.html
      args = ["get-token", "--login", "azurecli",
      "--server-id", "6dae42f8-4368-4678-94ff-3960e28e3630"]
    }
  }
}
