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

variable "certificate_arn" {
  description = "ACM cert ARN for the ALB HTTPS listener. Empty = HTTP-only (no custom domain)."
  type        = string
  default     = ""
}

variable "enable_https" {
  description = "Create the HTTPS:443 listener. Must be known at plan time (a bool from the caller), unlike certificate_arn which is computed during apply."
  type        = bool
  default     = false
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

variable "enable_tracing" {
  description = "Run the ADOT collector sidecar and grant the task role X-Ray write, so the app's OTLP traces reach AWS X-Ray. Off by default — the task role stays empty until tracing is opted into."
  type        = bool
  default     = false
}

variable "adot_image" {
  description = "AWS Distro for OpenTelemetry collector image for the tracing sidecar."
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.43.2"
}
