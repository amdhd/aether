variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB."
  type        = list(string)
}

variable "app_subnet_ids" {
  description = "Private subnets for the Fargate tasks."
  type        = list(string)
}

variable "alb_sg_id" {
  type = string
}

variable "api_sg_id" {
  type = string
}

variable "image" {
  description = "Full ECR image reference (repo:tag)."
  type        = string
}

variable "api_port" {
  type    = number
  default = 8000
}

variable "cpu" {
  description = "Fargate task CPU units (512 = 0.5 vCPU)."
  type        = number
  default     = 512
}

variable "memory" {
  description = "Fargate task memory (MiB)."
  type        = number
  default     = 1024
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "environment" {
  description = "Plain (non-secret) env vars injected into the container."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Secret env vars: map of ENV_NAME => Secrets Manager valueFrom ARN (optionally with :json-key:: suffix). Resolved by ECS at task start."
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "Base secret ARNs the execution role may read (for least-privilege IAM scoping)."
  type        = list(string)
  default     = []
}

variable "alb_idle_timeout" {
  description = "ALB idle timeout (s). Raised above the 60s default so long SSE chat streams aren't cut off."
  type        = number
  default     = 300
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "high_availability" {
  description = "Enables ECS service auto scaling (requires the Redis-backed rate limiter — keep coupled to the app's HA config)."
  type        = bool
  default     = false
}

variable "max_capacity" {
  description = "Upper bound for auto scaling when high_availability is true."
  type        = number
  default     = 4
}
