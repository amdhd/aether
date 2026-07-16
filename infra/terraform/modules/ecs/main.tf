data "aws_region" "current" {}

# --- Logs ---
resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name_prefix}-api"
  retention_in_days = var.log_retention_days
  tags              = { Name = "${var.name_prefix}-api-logs" }
}

# --- IAM: two roles (execution vs task), least privilege ---
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: what the ECS agent needs to START the task — pull the image,
# read the specific secrets, write logs.
resource "aws_iam_role" "execution" {
  name_prefix        = "${var.name_prefix}-exec-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Name = "${var.name_prefix}-exec" }
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "secrets_read" {
  count = length(var.secret_arns) > 0 ? 1 : 0
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count  = length(var.secret_arns) > 0 ? 1 : 0
  name   = "read-app-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.secrets_read[0].json
}

# Task role: what the APP itself may call in AWS at runtime. This app talks to
# external HTTP APIs (DeepSeek/OpenAI/Tavily/Google), not AWS, so it needs no AWS
# permissions — the empty role documents that intent explicitly.
resource "aws_iam_role" "task" {
  name_prefix        = "${var.name_prefix}-task-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Name = "${var.name_prefix}-task" }
}

# --- Task definition ---
resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "ARM64" # Graviton — cheaper than x86 for the same perf
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "api"
    image     = var.image
    essential = true
    portMappings = [{
      containerPort = var.api_port
      protocol      = "tcp"
    }]
    environment = [for k, v in var.environment : { name = k, value = v }]
    secrets     = [for k, v in var.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "api"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://localhost:${var.api_port}/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
  }])

  tags = { Name = "${var.name_prefix}-api" }
}

# --- Load balancer ---
resource "aws_lb" "api" {
  name                       = "${var.name_prefix}-alb"
  load_balancer_type         = "application"
  security_groups            = [var.alb_sg_id]
  subnets                    = var.public_subnet_ids
  idle_timeout               = var.alb_idle_timeout
  drop_invalid_header_fields = true
  tags                       = { Name = "${var.name_prefix}-alb" }
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name_prefix}-api-tg"
  port        = var.api_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${var.name_prefix}-api-tg" }
}

# HTTP listener. For a custom domain, add an ACM cert + an HTTPS:443 listener and
# redirect 80→443; the demo runs on HTTP behind CloudFront's HTTPS edge.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# --- Cluster + service ---
resource "aws_ecs_cluster" "this" {
  name = var.name_prefix
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = { Name = var.name_prefix }
}

resource "aws_ecs_service" "api" {
  name            = "${var.name_prefix}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # Give the task time to boot (image pull + uvicorn start) before the ALB
  # health check can cause ECS to cycle it.
  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = var.app_subnet_ids
    security_groups  = [var.api_sg_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = var.api_port
  }

  # Self-healing deploys: if the new task fails to reach a steady state, ECS
  # rolls back to the last-good task definition automatically (the CD pipeline's
  # safety net — a bad image can't take the service down).
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Ignore image-tag drift (CD registers new task-def revisions out of band) and
  # desired_count (auto scaling owns it when HA is on).
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [aws_lb_listener.http]
  tags       = { Name = "${var.name_prefix}-api" }
}

# --- Auto scaling (HA only) ---
resource "aws_appautoscaling_target" "api" {
  count              = var.high_availability ? 1 : 0
  max_capacity       = var.max_capacity
  min_capacity       = var.desired_count
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "cpu" {
  count              = var.high_availability ? 1 : 0
  name               = "${var.name_prefix}-cpu-target"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api[0].resource_id
  scalable_dimension = aws_appautoscaling_target.api[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.api[0].service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 60
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}
