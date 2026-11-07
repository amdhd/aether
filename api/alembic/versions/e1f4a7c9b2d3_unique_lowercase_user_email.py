"""Enforce one account per address regardless of case.

Revision ID: e1f4a7c9b2d3
Revises: d2a6b8c0e4f1

Addresses are normalised (trimmed, lowercased) on the way in from now on, but
rows written earlier keep their original casing and may already collide. The
unique index on lower(email) is what makes "Alice@x.com" and "alice@x.com" one
account at the database level.

Existing rows are not rewritten: lookups compare lower(email), so a legacy
mixed-case row is still found. If two accounts already differ only by case the
index cannot be built; the upgrade stops and names them, because choosing which
account survives is a decision for an operator, not a migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "e1f4a7c9b2d3"
down_revision = "d2a6b8c0e4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    duplicates = op.get_bind().execute(sa.text(
        "SELECT lower(email) AS address, count(*) AS accounts FROM users "
        "GROUP BY lower(email) HAVING count(*) > 1"
    )).fetchall()
    if duplicates:
        raise RuntimeError(
            f"{len(duplicates)} email address(es) belong to more than one account when "
            "compared case-insensitively. Merge or rename those accounts, then re-run "
            "the migration. Find them with: SELECT lower(email), array_agg(id) FROM users "
            "GROUP BY lower(email) HAVING count(*) > 1;"
        )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
