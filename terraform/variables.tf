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
  type        = string
  default     = "Foundry Lab"
}

variable "project_description" {
  type        = string
  default     = "MVP Foundry project managed by Terraform."
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
  type        = string
  default     = "claude-haiku-4-5"
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

variable "tags" {
  type = map(string)
  default = {
    project   = "foundry-lab"
    managedBy = "terraform"
  }
}
