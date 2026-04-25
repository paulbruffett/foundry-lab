terraform {
  backend "azurerm" {
    # Values provided via -backend-config in CI (see .github/workflows/terraform.yml).
    # Auth via service principal client secret (ARM_CLIENT_ID/SECRET/TENANT_ID env vars).
    use_azuread_auth = true
  }
}
