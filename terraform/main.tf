data "azurerm_cognitive_account" "foundry" {
  name                = var.foundry_account_name
  resource_group_name = var.foundry_account_resource_group
}

# The parent account must have `allowProjectManagement = true`.
# Verify once with:
#   az cognitiveservices account show -n <name> -g <rg> --query properties.allowProjectManagement
# If false, set it via an ARM/azapi update on the account before this applies.
resource "azapi_resource" "project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name      = var.project_name
  parent_id = data.azurerm_cognitive_account.foundry.id
  location  = var.location

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      displayName = var.project_display_name
      description = var.project_description
    }
  }

  tags = var.tags

  response_export_values = ["identity.principalId", "properties.endpoints"]
}

# Claude Haiku 4.5 deployment on the parent Foundry account.
# Requires:
#   - Enterprise or MCA-E subscription
#   - Marketplace agreement signed for Anthropic (Microsoft.SaaS register + Anthropic offer accept)
#   - Region: eastus2 or swedencentral
resource "azapi_resource" "claude_haiku" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-07-01-preview"
  name      = var.haiku_deployment_name
  parent_id = data.azurerm_cognitive_account.foundry.id

  body = {
    properties = {
      model = {
        format    = "Anthropic"
        name      = "claude-haiku-4-5"
        version   = var.haiku_model_version
        publisher = "Anthropic"
      }
      modelProviderData = {
        industry         = var.model_provider_industry
        organizationName = var.model_provider_organization_name
        countryCode      = var.model_provider_country_code
      }
      raiPolicyName = "Microsoft.Nill"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = var.haiku_capacity
    }
  }

  tags = merge(var.tags, {
    modelCard = "agent/model_card.md"
  })

  schema_validation_enabled = false
  response_export_values    = ["*"]
}
