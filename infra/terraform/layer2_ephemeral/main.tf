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

  # A custom domain turns on HTTPS end-to-end (ACM cert + ALB HTTPS listener).
  custom_domain = var.api_domain_name != ""
  api_url       = local.custom_domain ? "https://${var.api_domain_name}" : "http://${module.ecs.alb_dns_name}"

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
  # tracked separately; the infra just makes the store available. rediss:// = TLS
  # (transit encryption is on); the app connects to the primary endpoint.
  redis_environment = var.high_availability ? {
    REDIS_URL = "rediss://${aws_elasticache_replication_group.redis[0].primary_endpoint_address}:6379"
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

  # When a domain is configured, hand the validated cert to the ALB HTTPS listener.
  certificate_arn = local.custom_domain ? aws_acm_certificate_validation.api[0].certificate_arn : ""

  high_availability = var.high_availability
}

# --- Custom domain + TLS (only when api_domain_name is set) ---
data "aws_route53_zone" "main" {
  count = local.custom_domain ? 1 : 0
  name  = var.hosted_zone_name
}

resource "aws_acm_certificate" "api" {
  count             = local.custom_domain ? 1 : 0
  domain_name       = var.api_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
  tags = { Name = "${var.name_prefix}-api-cert" }
}

# DNS records that prove domain ownership to ACM.
resource "aws_route53_record" "cert_validation" {
  for_each = local.custom_domain ? {
    for dvo in aws_acm_certificate.api[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  } : {}

  zone_id         = data.aws_route53_zone.main[0].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "api" {
  count                   = local.custom_domain ? 1 : 0
  certificate_arn         = aws_acm_certificate.api[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# Point the API FQDN at the ALB.
resource "aws_route53_record" "api" {
  count   = local.custom_domain ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = var.api_domain_name
  type    = "A"

  alias {
    name                   = module.ecs.alb_dns_name
    zone_id                = module.ecs.alb_zone_id
    evaluate_target_health = true
  }
}

# --- ElastiCache Redis (HA only): Multi-AZ replication group w/ auto-failover ---
resource "aws_elasticache_subnet_group" "redis" {
  count      = var.high_availability ? 1 : 0
  name       = "${var.name_prefix}-redis"
  subnet_ids = module.vpc.app_subnet_ids
}

resource "aws_elasticache_replication_group" "redis" {
  count                = var.high_availability ? 1 : 0
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Aether shared rate-limit / cache store"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type
  port           = 6379

  # Primary + one replica, placed in different AZs, with automatic failover.
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true

  subnet_group_name  = aws_elasticache_subnet_group.redis[0].name
  security_group_ids = [module.security_groups.redis_sg_id]

  # Encryption at rest and in transit (clients connect via rediss://).
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  apply_immediately = true
  tags              = { Name = "${var.name_prefix}-redis" }
}

# --- VPC interface endpoints (PrivateLink) ---
# Keep ECR pulls, secret fetches, and log shipping on the AWS private backbone
# instead of egressing through NAT. (The free S3 gateway endpoint for ECR layers
# is created in the VPC module.) External LLM/tool APIs still use NAT.
locals {
  interface_endpoints = var.enable_vpc_endpoints ? toset([
    "ecr.api", "ecr.dkr", "secretsmanager", "logs",
  ]) : toset([])
}

resource "aws_security_group" "vpce" {
  count       = var.enable_vpc_endpoints ? 1 : 0
  name_prefix = "${var.name_prefix}-vpce-"
  description = "VPC interface endpoints: HTTPS from the API tasks"
  vpc_id      = module.vpc.vpc_id
  tags        = { Name = "${var.name_prefix}-vpce" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "vpce_https" {
  count                        = var.enable_vpc_endpoints ? 1 : 0
  security_group_id            = aws_security_group.vpce[0].id
  description                  = "HTTPS from the API tasks"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = module.security_groups.api_sg_id
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.interface_endpoints
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.app_subnet_ids
  security_group_ids  = [aws_security_group.vpce[0].id]
  private_dns_enabled = true
  tags                = { Name = "${var.name_prefix}-vpce-${each.value}" }
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

  # AWS-managed rule groups: common web exploits + known-bad inputs (the latter
  # covers Log4Shell / CVE-2021-44228). Free beyond the base WAF cost.
  rule {
    name     = "aws-common"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-aws-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 3
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-known-bad-inputs"
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
  name              = "${var.name_prefix}-alerts"
  kms_master_key_id = "alias/aws/sns" # encrypt at rest with the AWS-managed key
  tags              = { Name = "${var.name_prefix}-alerts" }
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

# --- Single-pane service-health dashboard (ALB · ECS · RDS) ---
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name_prefix}-service-health"
  dashboard_body = jsonencode({
    widgets = [
      {
        type       = "text", x = 0, y = 0, width = 24, height = 1,
        properties = { markdown = "# Aether — service health · ${var.environment}" }
      },
      {
        type = "metric", x = 0, y = 1, width = 12, height = 6,
        properties = {
          title = "ALB — requests & 5xx", region = var.aws_region, view = "timeSeries", period = 60
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", module.ecs.alb_arn_suffix, { stat = "Sum", label = "Requests" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", module.ecs.alb_arn_suffix, { stat = "Sum", label = "5xx" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 1, width = 12, height = 6,
        properties = {
          title = "ALB — latency (s)", region = var.aws_region, view = "timeSeries", period = 60
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", module.ecs.alb_arn_suffix, { stat = "Average", label = "avg" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", module.ecs.alb_arn_suffix, { stat = "p99", label = "p99" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 7, width = 12, height = 6,
        properties = {
          title = "ECS Fargate — CPU & memory %", region = var.aws_region, view = "timeSeries", period = 60
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", module.ecs.cluster_name, "ServiceName", module.ecs.service_name, { stat = "Average", label = "CPU %" }],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", module.ecs.cluster_name, "ServiceName", module.ecs.service_name, { stat = "Average", label = "Mem %" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 7, width = 12, height = 6,
        properties = {
          title = "RDS — CPU % & connections", region = var.aws_region, view = "timeSeries", period = 60
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", module.rds.instance_identifier, { stat = "Average", label = "CPU %" }],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", module.rds.instance_identifier, { stat = "Average", label = "Connections" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 13, width = 12, height = 6,
        properties = {
          title = "RDS — free storage (bytes)", region = var.aws_region, view = "timeSeries", period = 300
          metrics = [
            ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", module.rds.instance_identifier, { stat = "Average", label = "Free storage" }]
          ]
        }
      }
    ]
  })
}
