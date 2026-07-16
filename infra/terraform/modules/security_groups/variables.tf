variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "api_port" {
  description = "Container port the FastAPI app listens on."
  type        = number
  default     = 8000
}

variable "enable_redis" {
  description = "Create the Redis security group (HA only)."
  type        = bool
  default     = false
}
