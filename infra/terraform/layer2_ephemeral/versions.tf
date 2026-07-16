terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Separate state from layer1 so `terraform destroy` here tears down the whole
  # hourly footprint without touching the persistent base.
  #   terraform init -backend-config=backend.hcl
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Service     = "aether"
      Environment = var.environment
    }
  }
}
