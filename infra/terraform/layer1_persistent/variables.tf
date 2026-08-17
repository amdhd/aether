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

# --- Custom domain for the SPA frontend (CloudFront) ---
variable "frontend_domain_name" {
  description = "Apex domain to serve the SPA on, e.g. ahmadhadi.info. Empty = default *.cloudfront.net HTTPS."
  type        = string
  default     = ""
}

variable "hosted_zone_name" {
  description = "Route53 hosted zone that owns frontend_domain_name (e.g. ahmadhadi.info). Required when frontend_domain_name is set."
  type        = string
  default     = ""
}

# --- Content-Security-Policy ---
variable "api_origin" {
  description = <<-EOT
    Origin the SPA calls the API on, e.g. https://api.ahmadhadi.info. Used only
    to build the CSP's connect-src: the API lives on a different origin to the
    SPA, so 'self' does not cover it and the browser would block every request.

    This duplicates layer2's api_domain_name because CloudFront lives here and
    layer2 is applied afterwards, so layer1 cannot read it. Set both to the same
    host.

    Left empty, connect-src falls back to 'self' https:, which keeps the policy
    working for any API location at the cost of the one directive that would
    otherwise stop an injected script exfiltrating to an attacker's host. Set it.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.api_origin == "" || can(regex("^https?://[^/]+$", var.api_origin))
    error_message = "api_origin must be a bare scheme+host with no trailing path, e.g. https://api.example.com."
  }
}
