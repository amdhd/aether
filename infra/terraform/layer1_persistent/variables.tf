variable "aws_region" {
  type    = string
  default = "ap-southeast-1" # Singapore — closest to Malaysia (data.gov.my)
}

variable "environment" {
  description = "Deployment environment tag (org policy allows Dev/Stage/Prod)."
  type        = string
  default     = "Prod"

  validation {
    condition     = contains(["Dev", "Stage", "Prod"], var.environment)
    error_message = "environment must be one of Dev, Stage, Prod (org tagging policy)."
  }
}

variable "name_prefix" {
  type    = string
  default = "aether"
}

variable "budget_alert_email" {
  description = "Email for the monthly cost-budget alarm. Empty disables the budget (set it — it's your safety net if you forget to destroy layer2)."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly spend threshold that triggers the alert email."
  type        = number
  default     = 10
}
