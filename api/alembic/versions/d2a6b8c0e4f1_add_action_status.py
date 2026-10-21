"""Persist tool action outcomes.

Revision ID: d2a6b8c0e4f1
Revises: 971147ab7951
"""
from alembic import op
import sqlalchemy as sa

revision = "d2a6b8c0e4f1"
down_revision = "971147ab7951"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("action_status", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "action_status")
