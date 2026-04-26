variable "subscription_id" {
  type        = string
  description = "Azure subscription that owns the existing Foundry account."
}

variable "foundry_account_name" {
  type        = string
  description = "Name of the existing Microsoft.CognitiveServices account (kind = AIServices) acting as the Foundry workspace."
}

variable "foundry_account_resource_group" {
  type        = string
  description = "Resource group of the existing Foundry account."
}

variable "project_name" {
  type        = string
  description = "Foundry project to create under the existing account."
  default     = "foundry-lab"
}

variable "project_display_name" {
  type    = string
  default = "Foundry Lab"
}

variable "project_description" {
  type    = string
  default = "MVP Foundry project managed by Terraform."
}

variable "location" {
  type        = string
  description = "Region for the project. Claude Haiku is only available in eastus2 and swedencentral."
  default     = "eastus2"

  validation {
    condition     = contains(["eastus2", "swedencentral"], var.location)
    error_message = "Claude Haiku 4.5 requires eastus2 or swedencentral."
  }
}

variable "haiku_deployment_name" {
  type    = string
  default = "claude-haiku-4-5"
}

variable "haiku_model_version" {
  type        = string
  description = "Anthropic Haiku 4.5 catalog version tag on Foundry."
  default     = "20251001"
}

variable "haiku_capacity" {
  type        = number
  description = "GlobalStandard capacity units (1 unit = 1K TPM)."
  default     = 1
}

variable "model_provider_industry" {
  type        = string
  description = "Industry of the deploying organization. Required by Anthropic Marketplace."
  default     = "Technology"
}

variable "model_provider_organization_name" {
  type        = string
  description = "Legal organization name of the deploying customer. Required by Anthropic Marketplace."
  default     = "Foundry Lab"
}

variable "model_provider_country_code" {
  type        = string
  description = "ISO 3166-1 alpha-2 country code of the deploying organization."
  default     = "US"
}

variable "tags" {
  type = map(string)
  default = {
    project   = "foundry-lab"
    managedBy = "terraform"
  }
  description = "Tags applied to managed resources."
}

variable "a2a_resource_group_name" {
  type        = string
  description = "Pre-existing resource group that holds the A2A wrapper (ACR + Container Apps). Created out-of-band like the TF state RG; SP needs Contributor on it."
}

variable "acr_name" {
  type        = string
  description = "Globally unique ACR name (alphanumeric, 5-50 chars) for the A2A wrapper image."
}

variable "container_app_name" {
  type        = string
  description = "Container App name for the A2A wrapper."
  default     = "foundry-lab-a2a"
}

variable "a2a_aad_client_id" {
  type        = string
  description = "Entra app registration client ID used as the EasyAuth audience on the A2A Container App. Created out-of-band; see README."
}
