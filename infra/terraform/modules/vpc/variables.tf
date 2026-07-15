variable "name_prefix" {
  description = "Prefix for resource names, e.g. \"aether\"."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR block."
  type        = string
  default     = "10.0.0.0/16"
}

variable "azs" {
  description = "Availability zones to span (2 recommended)."
  type        = list(string)
}

variable "high_availability" {
  description = "When true, provision one NAT gateway per AZ (no cross-AZ egress SPOF). When false, a single NAT gateway serves all AZs to save cost — the demo default."
  type        = bool
  default     = false
}
