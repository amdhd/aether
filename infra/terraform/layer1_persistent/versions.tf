terraform {
  # use_lockfile (S3-native state locking) requires Terraform >= 1.10.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Remote state with locking. Supply the bucket/key/region at init time:
  #   terraform init -backend-config=backend.hcl
  # S3-native state locking (use_lockfile) needs no DynamoDB table.
  # Run `terraform validate` offline with `-backend=false`.
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

# CloudFront viewer certificates must live in us-east-1, regardless of the app
# region — used only for the optional custom frontend domain.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Service     = "aether"
      Environment = var.environment
    }
  }
}
