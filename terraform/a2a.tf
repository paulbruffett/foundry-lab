# A2A wrapper hosting — Container Apps fronting agent/a2a_server.py.
# Foundry has no native A2A endpoint; this wrapper proxies into the
# Foundry data plane and is what gets registered in Foundry Control Plane.

data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "a2a" {
  name = var.a2a_resource_group_name
}

resource "azurerm_container_registry" "a2a" {
  name                = var.acr_name
  resource_group_name = data.azurerm_resource_group.a2a.name
  location            = data.azurerm_resource_group.a2a.location
  sku                 = "Basic"
  admin_enabled       = false

  tags = var.tags
}

resource "azurerm_log_analytics_workspace" "a2a" {
  name                = "${var.container_app_name}-logs"
  resource_group_name = data.azurerm_resource_group.a2a.name
  location            = data.azurerm_resource_group.a2a.location
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = var.tags
}

resource "azurerm_container_app_environment" "a2a" {
  name                       = "${var.container_app_name}-env"
  resource_group_name        = data.azurerm_resource_group.a2a.name
  location                   = data.azurerm_resource_group.a2a.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.a2a.id

  tags = var.tags
}

# User-assigned identity for the Container App. Created and granted AcrPull
# *before* the app itself, which breaks the chicken-and-egg you'd hit with a
# system-assigned identity (Container Apps validates registry credentials at
# create time and the SAI can't be granted AcrPull until the app exists).
resource "azurerm_user_assigned_identity" "a2a" {
  name                = "${var.container_app_name}-id"
  resource_group_name = data.azurerm_resource_group.a2a.name
  location            = data.azurerm_resource_group.a2a.location
  tags                = var.tags
}

resource "azurerm_role_assignment" "a2a_acr_pull" {
  scope                = azurerm_container_registry.a2a.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.a2a.principal_id
}

resource "azurerm_container_app" "a2a" {
  name                         = var.container_app_name
  resource_group_name          = data.azurerm_resource_group.a2a.name
  container_app_environment_id = azurerm_container_app_environment.a2a.id
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.a2a.id]
  }

  registry {
    server   = azurerm_container_registry.a2a.login_server
    identity = azurerm_user_assigned_identity.a2a.id
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  secret {
    name  = "appinsights-connection-string"
    value = azurerm_application_insights.foundry.connection_string
  }

  template {
    min_replicas = 0
    max_replicas = 1

    container {
      name = "a2a-server"
      # Public placeholder that listens on 8080 (matches ingress.target_port,
      # so the bootstrap revision goes healthy). CI replaces this with the
      # ACR image; ignore_changes below stops subsequent applies from rolling
      # the deployed image back to this bootstrap value.
      image  = "nginxinc/nginx-unprivileged:alpine"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "PROJECT_ENDPOINT"
        value = try(azapi_resource.project.output.properties.endpoints["AI Foundry API"], "")
      }
      env {
        name  = "HAIKU_DEPLOYMENT_NAME"
        value = azapi_resource.claude_haiku.name
      }
      env {
        name  = "A2A_PUBLIC_URL"
        value = "https://${var.container_app_name}.${azurerm_container_app_environment.a2a.default_domain}"
      }
      # DefaultAzureCredential needs this hint to pick the user-assigned
      # identity — without it ManagedIdentityCredential 400s with invalid_scope
      # because the Container Apps MI endpoint can't infer which UAI to mint.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.a2a.client_id
      }
      env {
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection-string"
      }
    }
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  depends_on = [azurerm_role_assignment.a2a_acr_pull]
}

# Container App identity calls the Foundry data plane to invoke Claude via the
# Anthropic pass-through. Role must include the `Microsoft.CognitiveServices/
# accounts/AIServices/providers/action` data action — `Azure AI Developer`
# only covers OpenAI/SpeechServices/ContentSafety/MaaS data actions and gets
# rejected by /anthropic/v1/* with PermissionDenied. `Cognitive Services User`
# carries the broader `Microsoft.CognitiveServices/*` wildcard, which is the
# minimum built-in role that grants access to the AIServices scope.
resource "azurerm_role_assignment" "a2a_foundry_data_plane" {
  scope                = data.azurerm_cognitive_account.foundry.id
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.a2a.principal_id
}

# EasyAuth (Container Apps authConfig). azurerm doesn't model this resource;
# using azapi against Microsoft.App/containerApps/authConfigs.
# Pre-req: the Entra app reg referenced by var.a2a_aad_client_id exists with
# App ID URI = api://<clientId>. See README for bootstrap.
resource "azapi_resource" "a2a_easyauth" {
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.a2a.id

  body = {
    properties = {
      platform = {
        enabled = true
      }
      globalValidation = {
        unauthenticatedClientAction = "Return401"
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            openIdIssuer = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
            clientId     = var.a2a_aad_client_id
          }
          validation = {
            allowedAudiences = ["api://${var.a2a_aad_client_id}"]
          }
        }
      }
    }
  }

  schema_validation_enabled = false
}
