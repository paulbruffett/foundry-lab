terraform {
  backend "azurerm" {
    # Values provided via -backend-config in CI (see .github/workflows/terraform.yml).
    # Bootstrap the state container once:
    #   az group create -n tfstate-rg -l eastus2
    #   az storage account create -n <unique> -g tfstate-rg -l eastus2 --sku Standard_LRS
    #   az storage container create -n tfstate --account-name <unique>
    use_oidc = true
  }
}
