variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}

variable "environment" {
  type    = string
  default = "Prod"

  validation {
    condition     = contains(["Dev", "Stage", "Prod"], var.environment)
    error_message = "environment must be one of Dev, Stage, Prod (org tagging policy)."
  }
}

variable "name_prefix" {
  type    = string
  default = "aether"
}

variable "azs" {
  description = "Two AZs to span."
  type        = list(string)
  default     = ["ap-southeast-1a", "ap-southeast-1b"]
}

# --- The single HA toggle: restructures the whole stack (principle #4) ---
variable "high_availability" {
  description = "false = cheap, single-AZ, 1-task demo. true = Multi-AZ RDS, autoscaling, ElastiCache Redis."
  type        = bool
  default     = false
}

# --- Sizing knobs (no hardcoded literals in resources; principle #1) ---
variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "ecs_task_cpu" {
  type    = number
  default = 512
}

variable "ecs_task_memory" {
  type    = number
  default = 1024
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

# --- Image + remote state wiring ---
variable "image_tag" {
  description = "Tag of the API image in ECR to run."
  type        = string
  default     = "latest"
}

variable "layer1_state_bucket" {
  description = "S3 bucket holding layer1's remote state."
  type        = string
}

variable "layer1_state_key" {
  description = "State key/path for layer1 within the bucket."
  type        = string
  default     = "aether/layer1_persistent/terraform.tfstate"
}

variable "alert_email" {
  description = "Optional email for CloudWatch alarm notifications. Empty disables the subscription."
  type        = string
  default     = ""
}

# --- Custom domain for the API (enables HTTPS on the ALB) ---
variable "api_domain_name" {
  description = "FQDN to serve the API on, e.g. api.example.com. Empty = HTTP-only demo (no TLS on the ALB)."
  type        = string
  default     = ""
}

variable "hosted_zone_name" {
  description = "Route53 hosted zone that owns api_domain_name, e.g. example.com. Required when api_domain_name is set."
  type        = string
  default     = ""
}

variable "waf_rate_limit" {
  description = "Max requests per 5-minute window per IP before WAF blocks (protects the LLM endpoint from cost abuse)."
  type        = number
  default     = 1000
}

variable "enable_vpc_endpoints" {
  description = "Create VPC interface endpoints (ECR api/dkr, Secrets Manager, CloudWatch Logs) so those calls use PrivateLink instead of NAT. On in both demo and HA; set false to trim cost."
  type        = bool
  default     = true
}
