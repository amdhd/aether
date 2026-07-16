terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Remote state with locking. Supply the bucket/key/region at init time:
  #   terraform init -backend-config=backend.hcl
  # (S3 native lockfile via use_lockfile — no DynamoDB table required on AWS
  # provider v5.60+.) Run `terraform validate` offline with `-backend=false`.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.aws_region

  # Every resource in this stack is tagged automatically — satisfies the org
  # tagging policy (Service + Environment) without per-resource repetition.
  default_tags {
    tags = {
      Service     = "aether"
      Environment = var.environment
    }
  }
}
