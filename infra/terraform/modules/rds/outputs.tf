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

# Master (owner) login. Used only by the migration task, which runs DDL and
# creates the app role below.
output "database_url" {
  # ssl=verify-full: encrypt, and check the server certificate against the RDS
  # CA bundle baked into the image (PGSSLROOTCERT) and its hostname against this
  # address. asyncpg's default, "prefer", encrypts when it can but verifies
  # nothing, so anything that can answer on the database's address is trusted.
  value     = "postgresql+asyncpg://${var.master_username}:${var.master_password}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}?ssl=verify-full"
  sensitive = true
}

# What the API service uses: the DML-only role ensure_app_role (api/app/db/
# app_role.py) creates during migrations. Same TLS verification as above.
output "app_database_url" {
  value     = "postgresql+asyncpg://${var.app_username}:${var.app_password}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}?ssl=verify-full"
  sensitive = true
}
