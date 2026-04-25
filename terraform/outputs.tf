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
