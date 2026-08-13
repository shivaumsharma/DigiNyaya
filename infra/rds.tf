resource "aws_db_subnet_group" "diginyaya" {
  name       = "diginyaya-${var.environment}"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_db_instance" "diginyaya" {
  identifier     = "diginyaya-${var.environment}"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.diginyaya.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az                = false # Single-AZ -- cost control, matches the "one instance" scope boundary

  backup_retention_period = 7
  # Guards against an accidental `terraform destroy` silently losing real
  # data once this holds production cases -- see the migration plan's
  # "check in before acting" list.
  deletion_protection = true
  skip_final_snapshot  = false
  final_snapshot_identifier = "diginyaya-${var.environment}-final"

  tags = {
    Name = "diginyaya-${var.environment}"
  }
}
