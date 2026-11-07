"""The least-privilege database role the running API connects as.

The service used to log in as the RDS master user, so a SQL injection, a
compromised dependency or a leaked DATABASE_URL could drop tables, alter the
schema or create roles. Migrations need that power; serving requests does not.

`ensure_app_role` runs at the end of every `alembic upgrade` that is given
APP_DB_USER and APP_DB_PASSWORD (the ECS migration task is; the API service is
not). It creates or updates a LOGIN role that can read and write rows in the
application's tables and use their sequences, and nothing else: it owns no
table, cannot run DDL, and cannot touch alembic_version. Re-running it is safe
and also re-grants on tables a migration just created.

Postgres only. SQLite (local dev, tests) has no roles.
"""

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Strict shapes so both values can be written into DDL, which takes no bind
# parameters. The deploy stack generates them; anything else is refused.
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PASSWORD = re.compile(r"^[A-Za-z0-9]{24,128}$")

_MIGRATION_TABLE = "alembic_version"


def validate_app_role(user: str, password: str) -> None:
    if not _ROLE_NAME.fullmatch(user or ""):
        raise ValueError("APP_DB_USER must be a lowercase Postgres identifier")
    if not _PASSWORD.fullmatch(password or ""):
        raise ValueError("APP_DB_PASSWORD must be 24-128 letters and digits")


def ensure_app_role(connection: Connection, user: str, password: str) -> None:
    validate_app_role(user, password)
    if connection.dialect.name != "postgresql":
        return

    owner = connection.execute(text("SELECT current_user")).scalar_one()
    if owner == user:
        raise ValueError("APP_DB_USER must differ from the migration (owner) role")

    quote = connection.dialect.identifier_preparer.quote
    role = quote(user)
    database = quote(connection.execute(text("SELECT current_database()")).scalar_one())

    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": user}
    ).scalar()
    verb = "ALTER" if exists else "CREATE"
    statements = [
        f"{verb} ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
        f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}'",
        f"GRANT CONNECT ON DATABASE {database} TO {role}",
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"REVOKE CREATE ON SCHEMA public FROM {role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
        # Tables and sequences later migrations create are covered too.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
    ]
    if connection.execute(text("SELECT to_regclass(:t)"), {"t": _MIGRATION_TABLE}).scalar():
        statements.append(f"REVOKE ALL ON {quote(_MIGRATION_TABLE)} FROM {role}")
    for statement in statements:
        connection.exec_driver_sql(statement)
