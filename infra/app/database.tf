# Aurora PostgreSQL Serverless v2 in private subnets, reachable only through the RDS
# Data API (no NAT, no interface endpoints: design.md §4.3, verified in §9.1).

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_rds_engine_version" "pg16" {
  engine  = "aurora-postgresql"
  version = "16"
  latest  = true
}

resource "aws_vpc" "this" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "No ingress: Aurora is reached only through the RDS Data API"
  vpc_id      = aws_vpc.this.id
}

resource "aws_rds_cluster" "this" {
  cluster_identifier          = local.name
  engine                      = "aurora-postgresql"
  engine_mode                 = "provisioned"
  engine_version              = data.aws_rds_engine_version.pg16.version
  database_name               = "milewise"
  master_username             = "milewise_admin"
  manage_master_user_password = true
  enable_http_endpoint        = true
  db_subnet_group_name        = aws_db_subnet_group.this.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  storage_encrypted           = true
  backup_retention_period     = 1
  copy_tags_to_snapshot       = true
  # Everything in the DB is rebuildable (`make restore`), so no final snapshot (R15).
  skip_final_snapshot = true
  deletion_protection = false
  apply_immediately   = true

  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = 2
    seconds_until_auto_pause = 600
  }

  lifecycle {
    ignore_changes = [engine_version] # minor upgrades are applied by AWS
  }
}

resource "aws_rds_cluster_instance" "this" {
  identifier         = "${local.name}-1"
  cluster_identifier = aws_rds_cluster.this.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
}
