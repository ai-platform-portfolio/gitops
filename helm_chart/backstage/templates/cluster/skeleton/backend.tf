terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  backend "azurerm" {
    resource_group_name  = "${{ values.stateResourceGroup }}"
    storage_account_name = "${{ values.stateAccount }}"
    container_name       = "${{ values.stateContainer }}"
    key                  = "clusters/${{ values.name }}.tfstate"
    use_azuread_auth     = true
  }

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.81.0"
    }
  }
}

provider "azurerm" {
  features {}
  use_cli  = false
  use_oidc = true
}
