# Layered chain: internet → ALB → API → (RDS, Redis). Each tier only accepts
# traffic from the tier in front of it, referenced by security-group id.

resource "aws_security_group" "alb" {
  name_prefix = "${var.name_prefix}-alb-"
  description = "ALB: public HTTP/HTTPS ingress"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name_prefix}-alb" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from anywhere"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "All egress"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# --- API (Fargate) ---
resource "aws_security_group" "api" {
  name_prefix = "${var.name_prefix}-api-"
  description = "API: accepts traffic only from the ALB"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name_prefix}-api" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  description                  = "App port from ALB only"
  ip_protocol                  = "tcp"
  from_port                    = var.api_port
  to_port                      = var.api_port
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "api_all" {
  security_group_id = aws_security_group.api.id
  description       = "All egress (external LLM/tool APIs via NAT)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# --- RDS ---
resource "aws_security_group" "rds" {
  name_prefix = "${var.name_prefix}-rds-"
  description = "RDS: Postgres from the API only"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name_prefix}-rds" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_api" {
  security_group_id            = aws_security_group.rds.id
  description                  = "Postgres 5432 from API"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.api.id
}

# --- Redis (HA only) ---
resource "aws_security_group" "redis" {
  count       = var.enable_redis ? 1 : 0
  name_prefix = "${var.name_prefix}-redis-"
  description = "Redis: from the API only"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name_prefix}-redis" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_api" {
  count                        = var.enable_redis ? 1 : 0
  security_group_id            = aws_security_group.redis[0].id
  description                  = "Redis 6379 from API"
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
  referenced_security_group_id = aws_security_group.api.id
}
