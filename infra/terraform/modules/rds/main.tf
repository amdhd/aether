resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = var.subnet_ids
  tags       = { Name = "${var.name_prefix}-db-subnets" }
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name_prefix}-db"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.master_username
  password = var.master_password
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false

  # HA gates the production-safety knobs. In demo mode everything is tuned for a
  # fast, fully destroyable instance (principle #5: destroy must never hang).
  multi_az                  = var.high_availability
  deletion_protection       = var.high_availability
  skip_final_snapshot       = !var.high_availability
  final_snapshot_identifier = var.high_availability ? "${var.name_prefix}-db-final" : null
  backup_retention_period   = var.high_availability ? 7 : 0
  apply_immediately         = !var.high_availability

  # pgvector ships with RDS Postgres 16; the app's Alembic migration runs
  # CREATE EXTENSION vector. No parameter-group change required.

  tags = { Name = "${var.name_prefix}-db" }
}
