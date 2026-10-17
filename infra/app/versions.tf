# App stack: everything that `make down` destroys (design.md §0, R15).
# Persistent pieces (state, buckets, ECR, budgets, OIDC) live in infra/bootstrap.
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "milewise"
      Stack     = "app"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

data "terraform_remote_state" "bootstrap" {
  backend = "s3"
  config = {
    bucket = "milewise-tfstate-${data.aws_caller_identity.current.account_id}"
    key    = "bootstrap/terraform.tfstate"
    region = var.region
  }
}

locals {
  bootstrap = data.terraform_remote_state.bootstrap.outputs
  name      = "milewise"
}
