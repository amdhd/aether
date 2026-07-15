output "ecr_repository_url" {
  description = "Push the API image here."
  value       = aws_ecr_repository.api.repository_url
}

output "web_bucket" {
  description = "S3 bucket to sync the built SPA into."
  value       = aws_s3_bucket.web.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.web.id
}

output "cloudfront_domain_name" {
  description = "Public HTTPS URL of the frontend."
  value       = aws_cloudfront_distribution.web.domain_name
}

output "app_secret_arn" {
  description = "ARN of the app secrets container (consumed by layer2)."
  value       = aws_secretsmanager_secret.app.arn
}
