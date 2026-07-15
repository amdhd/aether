# =============================================================================
# Layer 2 — ephemeral. The hourly-billed stack: VPC, ALB, ECS, RDS, (Redis).
# `terraform apply` for a demo, `terraform destroy` after → back to ~$0.
# Consumes layer1's persistent outputs (ECR, secrets, CloudFront) via remote
# state; keeps its own state so teardown never touches the base.
# =============================================================================

data "terraform_remote_state" "layer1" {
  backend = "s3"
  config = {
    bucket = var.layer1_state_bucket
    key    = var.layer1_state_key
    region = var.aws_region
  }
}

locals {
  app_secret_arn    = data.terraform_remote_state.layer1.outputs.app_secret_arn
  ecr_repo_url      = data.terraform_remote_state.layer1.outputs.ecr_repository_url
  cloudfront_domain = data.terraform_remote_state.layer1.outputs.cloudfront_domain_name

  # Non-secret container config. Cross-origin (CloudFront SPA ↔ ALB API) needs
  # Secure + SameSite=None refresh cookies; TRUST_PROXY_HEADERS lets per-IP rate
  # limiting read X-Forwarded-For behind the ALB.
  base_environment = {
    ENVIRONMENT             = "production"
    TRUST_PROXY_HEADERS     = "true"
    REFRESH_COOKIE_SECURE   = "true"
    REFRESH_COOKIE_SAMESITE = "none"
    FRONTEND_ORIGIN         = "https://${local.cloudfront_domain}"
  }

  # Redis is provisioned only in HA mode. NOTE: the app's in-memory rate limiter
  # must be updated to read REDIS_URL before >1 task is safe — that app change is
  # tracked separately; the infra just makes the store available.
  redis_environment = var.high_availability ? {
    REDIS_URL = "redis://${aws_elasticache_cluster.redis[0].cache_nodes[0].address}:6379"
  } : {}

  container_environment = merge(local.base_environment, local.redis_environment)

  # Secret env vars resolved by ECS from Secrets Manager at task start
  # (valueFrom → ARN; principle #2). DATABASE_URL is the whole ephemeral secret;
  # the rest pull individual JSON keys out of the persistent app secret.
  container_secrets = {
    DATABASE_URL         = aws_secretsmanager_secret.db_url.arn
    SECRET_KEY           = "${local.app_secret_arn}:SECRET_KEY::"
    ENCRYPTION_KEY       = "${local.app_secret_arn}:ENCRYPTION_KEY::"
    DEEPSEEK_API_KEY     = "${local.app_secret_arn}:DEEPSEEK_API_KEY::"
    OPENAI_API_KEY       = "${local.app_secret_arn}:OPENAI_API_KEY::"
    TAVILY_API_KEY       = "${local.app_secret_arn}:TAVILY_API_KEY::"
    GOOGLE_CLIENT_ID     = "${local.app_secret_arn}:GOOGLE_CLIENT_ID::"
    GOOGLE_CLIENT_SECRET = "${local.app_secret_arn}:GOOGLE_CLIENT_SECRET::"
    GOOGLE_REDIRECT_URI  = "${local.app_secret_arn}:GOOGLE_REDIRECT_URI::"
  }
}

# --- Database credentials (ephemeral, lives with RDS) ---
resource "random_password" "db" {
  length  = 32
  special = false # keep the URL clean (no escaping in the connection string)
}

module "vpc" {
  source            = "../modules/vpc"
  name_prefix       = var.name_prefix
  azs               = var.azs
  high_availability = var.high_availability
}

module "security_groups" {
  source       = "../modules/security_groups"
  name_prefix  = var.name_prefix
  vpc_id       = module.vpc.vpc_id
  enable_redis = var.high_availability
}

module "rds" {
  source            = "../modules/rds"
  name_prefix       = var.name_prefix
  subnet_ids        = module.vpc.db_subnet_ids
  security_group_id = module.security_groups.rds_sg_id
  instance_class    = var.db_instance_class
  allocated_storage = var.db_allocated_storage
  master_password   = random_password.db.result
  high_availability = var.high_availability
}

# Assemble the full asyncpg URL into an ephemeral secret the task reads.
resource "aws_secretsmanager_secret" "db_url" {
  name                    = "${var.name_prefix}/database-url"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db_url" {
  secret_id     = aws_secretsmanager_secret.db_url.id
  secret_string = module.rds.database_url
}

module "ecs" {
  source            = "../modules/ecs"
  name_prefix       = var.name_prefix
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
  app_subnet_ids    = module.vpc.app_subnet_ids
  alb_sg_id         = module.security_groups.alb_sg_id
  api_sg_id         = module.security_groups.api_sg_id

  image         = "${local.ecr_repo_url}:${var.image_tag}"
  cpu           = var.ecs_task_cpu
  memory        = var.ecs_task_memory
  desired_count = var.api_desired_count

  environment = local.container_environment
  secrets     = local.container_secrets
  secret_arns = [local.app_secret_arn, aws_secretsmanager_secret.db_url.arn]

  high_availability = var.high_availability
}

# --- ElastiCache Redis (HA only) ---
resource "aws_elasticache_subnet_group" "redis" {
  count      = var.high_availability ? 1 : 0
  name       = "${var.name_prefix}-redis"
  subnet_ids = module.vpc.app_subnet_ids
}

resource "aws_elasticache_cluster" "redis" {
  count              = var.high_availability ? 1 : 0
  cluster_id         = "${var.name_prefix}-redis"
  engine             = "redis"
  node_type          = var.redis_node_type
  num_cache_nodes    = 1
  subnet_group_name  = aws_elasticache_subnet_group.redis[0].name
  security_group_ids = [module.security_groups.redis_sg_id]
  tags               = { Name = "${var.name_prefix}-redis" }
}

# --- WAF: rate-based rule on the ALB (protects the LLM endpoint from abuse) ---
resource "aws_wafv2_web_acl" "api" {
  name  = "${var.name_prefix}-api-waf"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "rate-limit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-api-waf"
    sampled_requests_enabled   = true
  }

  tags = { Name = "${var.name_prefix}-api-waf" }
}

resource "aws_wafv2_web_acl_association" "api" {
  resource_arn = module.ecs.alb_arn
  web_acl_arn  = aws_wafv2_web_acl.api.arn
}

# --- Alarms → SNS ---
resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-alerts"
  tags = { Name = "${var.name_prefix}-alerts" }
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.name_prefix}-alb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "API returning 5xx"
  dimensions          = { LoadBalancer = module.ecs.alb_arn_suffix }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = { Name = "${var.name_prefix}-alb-5xx" }
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${var.name_prefix}-unhealthy-hosts"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "One or more API targets unhealthy"
  dimensions = {
    LoadBalancer = module.ecs.alb_arn_suffix
    TargetGroup  = module.ecs.target_group_arn_suffix
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
  tags          = { Name = "${var.name_prefix}-unhealthy-hosts" }
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.name_prefix}-rds-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "RDS CPU sustained high"
  dimensions          = { DBInstanceIdentifier = module.rds.instance_identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = { Name = "${var.name_prefix}-rds-cpu" }
}
