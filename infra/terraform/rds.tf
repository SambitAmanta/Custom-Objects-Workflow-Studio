# AWS RDS PostgreSQL 16 Control Plane Database
resource "aws_db_subnet_group" "main" {
  name       = "${var.app_name}-${var.environment}-db-subnet-group"
  subnet_ids = [aws_subnet.private_1.id, aws_subnet.private_2.id]

  tags = {
    Name = "${var.app_name}-${var.environment}-db-subnet-group"
  }
}

resource "random_password" "db_password" {
  length           = 32
  special          = false
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_instance" "postgres" {
  identifier        = "${var.app_name}-${var.environment}-pg"
  engine            = "postgres"
  engine_version     = "16.2"
  instance_class    = "db.t4g.micro"
  allocated_storage = 20
  max_allocated_storage = 100
  storage_type      = "gp3"

  db_name  = "forge_db"
  username = "forge_admin"
  password = random_password.db_password.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  skip_final_snapshot     = true
  deletion_protection     = false
  backup_retention_period = 7
  publicly_accessible    = false

  tags = {
    Name        = "${var.app_name}-${var.environment}-postgres"
    Environment = var.environment
  }
}

# Store database credentials securely in AWS SSM Parameter Store
resource "aws_ssm_parameter" "database_url" {
  name  = "/${var.app_name}/${var.environment}/DATABASE_URL"
  type  = "SecureString"
  value = "postgres://${aws_db_instance.postgres.username}:${random_password.db_password.result}@${aws_db_instance.postgres.endpoint}/${aws_db_instance.postgres.db_name}"
}

