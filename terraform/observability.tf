# App Insights for Foundry project tracing. Workspace-based, backed by the
# Log Analytics workspace already provisioned for Container Apps logs — same
# sink for both A2A wrapper logs and Foundry traces.
resource "azurerm_application_insights" "foundry" {
  name                = "${var.project_name}-ai"
  location            = data.azurerm_resource_group.a2a.location
  resource_group_name = data.azurerm_resource_group.a2a.name
  workspace_id        = azurerm_log_analytics_workspace.a2a.id
  application_type    = "web"

  tags = var.tags
}

# Project connection of category "AppInsights". This is what makes the
# instance show up under the project's tracing/observability surface in the
# Foundry portal and what the azure-ai-projects SDK resolves when callers ask
# for the project's telemetry endpoint.
resource "azapi_resource" "project_appinsights_conn" {
  type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name      = "appinsights"
  parent_id = azapi_resource.project.id

  body = {
    properties = {
      category      = "AppInsights"
      target        = azurerm_application_insights.foundry.id
      authType      = "ApiKey"
      isSharedToAll = true
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.foundry.id
      }
      credentials = {
        key = azurerm_application_insights.foundry.connection_string
      }
    }
  }

  schema_validation_enabled = false
}
