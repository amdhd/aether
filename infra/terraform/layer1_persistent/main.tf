# =============================================================================
# Layer 1 — persistent base. Near-zero cost, left running between demos so the
# frontend URL, image, and secrets survive `make down`. Holds: ECR, the SPA
# bucket + CloudFront, and the app secrets.
# =============================================================================

data "aws_caller_identity" "current" {}

# --- ECR: API image ---
resource "aws_ecr_repository" "api" {
  name                 = "${var.name_prefix}-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # allow teardown even with images present

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep only the last 10 images so the repo doesn't grow unbounded.
resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire all but the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# --- S3: static SPA bucket (private; served only via CloudFront OAC) ---
resource "aws_s3_bucket" "web" {
  bucket        = "${var.name_prefix}-web-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # principle #5: a non-empty bucket must not block destroy
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "web" {
  bucket = aws_s3_bucket.web.id
  versioning_configuration {
    status = "Enabled"
  }
}

# --- CloudFront: HTTPS edge in front of the private bucket via OAC ---
resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.name_prefix}-web-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # cheapest edge footprint
  comment             = "${var.name_prefix} SPA"

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # AWS managed "CachingOptimized" policy.
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  # SPA history fallback: S3 returns 403 for unknown keys → serve index.html.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # free *.cloudfront.net HTTPS
  }
}

# Security response headers at the edge (no app code needed).
resource "aws_cloudfront_response_headers_policy" "security" {
  name = "${var.name_prefix}-security-headers"

  security_headers_config {
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }
}

# Bucket policy: only this CloudFront distribution may read objects.
data "aws_iam_policy_document" "web_bucket" {
  statement {
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json
}

# --- Secrets Manager: app secrets (persistent; values set out-of-band) ---
# Terraform creates the container and a placeholder version; real values are set
# via console/CLI and NOT tracked here (principle #3). ignore_changes keeps
# Terraform from clobbering them on later applies.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name_prefix}/app"
  description             = "Aether app secrets (SECRET_KEY, ENCRYPTION_KEY, API keys, Google OAuth)."
  recovery_window_in_days = 0 # allow immediate delete/recreate in non-prod
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    SECRET_KEY           = "REPLACE_ME"
    ENCRYPTION_KEY       = "REPLACE_ME"
    DEEPSEEK_API_KEY     = "REPLACE_ME"
    OPENAI_API_KEY       = ""
    TAVILY_API_KEY       = ""
    GOOGLE_CLIENT_ID     = ""
    GOOGLE_CLIENT_SECRET = ""
    GOOGLE_REDIRECT_URI  = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# --- Cost safety net: email if monthly spend crosses the threshold ---
# Persistent on purpose — it still warns you if a `make down` leaves orphans.
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_alert_email == "" ? 0 : 1
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
