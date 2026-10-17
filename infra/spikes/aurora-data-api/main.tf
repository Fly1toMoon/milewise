# Throwaway spike for design.md R1: does Aurora PostgreSQL Serverless v2 with
# min 0 ACU (auto-pause) + RDS Data API + pgvector work from outside the VPC?
# Destroy with `make spike-down` once scripts/spike_data_api.py has reported.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  backend "s3" {}
}

variable "region" {
  type    = string
  default = "ap-northeast-1"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "milewise"
      Stack     = "spike-aurora-data-api"
      ManagedBy = "terraform"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_rds_engine_version" "pg16" {
  engine  = "aurora-postgresql"
  version = "16"
  latest  = true
}

# Private-only VPC: no internet gateway, no NAT, no endpoints. Nothing inside
# needs to reach out; the Data API is the only way in.
resource "aws_vpc" "this" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "milewise-spike" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "milewise-spike-private-${count.index}" }
}

resource "aws_db_subnet_group" "this" {
  name       = "milewise-spike"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "db" {
  name        = "milewise-spike-db"
  description = "No ingress: access only through the RDS Data API"
  vpc_id      = aws_vpc.this.id
}

resource "aws_rds_cluster" "this" {
  cluster_identifier          = "milewise-spike"
  engine                      = "aurora-postgresql"
  engine_mode                 = "provisioned"
  engine_version              = data.aws_rds_engine_version.pg16.version
  database_name               = "milewise"
  master_username             = "milewise_admin"
  manage_master_user_password = true
  enable_http_endpoint        = true # RDS Data API
  db_subnet_group_name        = aws_db_subnet_group.this.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  storage_encrypted           = true
  skip_final_snapshot         = true
  deletion_protection         = false
  apply_immediately           = true

  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = 2
    seconds_until_auto_pause = 300 # shortest allowed, to measure resume quickly
  }
}

resource "aws_rds_cluster_instance" "this" {
  identifier         = "milewise-spike-1"
  cluster_identifier = aws_rds_cluster.this.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
}

output "cluster_arn" {
  value = aws_rds_cluster.this.arn
}

output "cluster_identifier" {
  value = aws_rds_cluster.this.cluster_identifier
}

output "master_secret_arn" {
  value = aws_rds_cluster.this.master_user_secret[0].secret_arn
}

output "database_name" {
  value = aws_rds_cluster.this.database_name
}

output "engine_version" {
  value = aws_rds_cluster.this.engine_version_actual
}
