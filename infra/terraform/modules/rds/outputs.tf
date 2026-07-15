output "address" {
  value = aws_db_instance.this.address
}

output "instance_identifier" {
  value = aws_db_instance.this.identifier
}

output "port" {
  value = aws_db_instance.this.port
}

output "db_name" {
  value = aws_db_instance.this.db_name
}

# Full async SQLAlchemy URL the app expects in DATABASE_URL.
output "database_url" {
  value     = "postgresql+asyncpg://${var.master_username}:${var.master_password}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}"
  sensitive = true
}
