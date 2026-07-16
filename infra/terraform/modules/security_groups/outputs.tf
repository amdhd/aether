output "alb_sg_id" {
  value = aws_security_group.alb.id
}

output "api_sg_id" {
  value = aws_security_group.api.id
}

output "rds_sg_id" {
  value = aws_security_group.rds.id
}

output "redis_sg_id" {
  value = var.enable_redis ? aws_security_group.redis[0].id : null
}
