terraform {
  # >= 1.10 for S3 native state locking (use_lockfile), so no DynamoDB table is needed.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "milewise"
      Stack     = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}
