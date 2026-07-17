output "api_alb_url" {
  description = "Public API base URL (HTTPS with a custom domain, else the HTTP ALB DNS). VITE_API_URL = this (the SPA appends /api/v1 itself); GOOGLE_REDIRECT_URI = this + /api/v1/integrations/google/callback."
  value       = local.api_url
}

output "api_uses_https" {
  description = "True when the API is served over HTTPS via a custom domain."
  value       = local.custom_domain
}

output "frontend_url" {
  description = "The persistent frontend URL (custom domain or CloudFront default, from layer1)."
  value       = local.frontend_url
}

output "ecs_cluster" {
  value = module.ecs.cluster_name
}

output "ecs_service" {
  value = module.ecs.service_name
}

output "task_definition_arn" {
  description = "Used by the one-off `alembic upgrade head` migration task."
  value       = module.ecs.task_definition_arn
}

output "log_group" {
  value = module.ecs.log_group_name
}

output "app_subnet_ids" {
  description = "Run the migration task in these subnets."
  value       = module.vpc.app_subnet_ids
}

output "api_security_group_id" {
  value = module.security_groups.api_sg_id
}
