output "api_alb_url" {
  description = "Public API entry point. Set VITE_API_URL to \"<this>/api/v1\" when building the SPA, and GOOGLE_REDIRECT_URI accordingly."
  value       = "http://${module.ecs.alb_dns_name}"
}

output "frontend_url" {
  description = "The persistent CloudFront SPA URL (from layer1)."
  value       = "https://${local.cloudfront_domain}"
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
