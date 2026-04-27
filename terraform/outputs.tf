output "foundry_account_id" {
  value = data.azurerm_cognitive_account.foundry.id
}

output "project_id" {
  value = azapi_resource.project.id
}

output "project_endpoint" {
  description = "Data-plane endpoint used by azure-ai-projects SDK."
  value       = try(azapi_resource.project.output.properties.endpoints["AI Foundry API"], null)
}

output "project_principal_id" {
  value = try(azapi_resource.project.output.identity.principalId, null)
}

output "haiku_deployment_name" {
  value = azapi_resource.claude_haiku.name
}

output "haiku_endpoint" {
  description = "Anthropic Messages API base URL on Foundry."
  value       = "${data.azurerm_cognitive_account.foundry.endpoint}anthropic/v1/messages"
}

output "acr_name" {
  value = azurerm_container_registry.a2a.name
}

output "acr_login_server" {
  value = azurerm_container_registry.a2a.login_server
}

output "container_app_name" {
  value = azurerm_container_app.a2a.name
}

output "a2a_resource_group" {
  value = data.azurerm_resource_group.a2a.name
}

output "a2a_public_url" {
  description = "Public FQDN of the A2A wrapper. Register {url}/.well-known/agent-card.json in Foundry Control Plane."
  value       = "https://${azurerm_container_app.a2a.ingress[0].fqdn}"
}

output "appinsights_id" {
  value = azurerm_application_insights.foundry.id
}

output "appinsights_connection_string" {
  description = "Application Insights connection string for the Foundry project. Use with OpenTelemetry exporters in agent code."
  value       = azurerm_application_insights.foundry.connection_string
  sensitive   = true
}
