"""add attachment columns to messages

Revision ID: b7e2d5f9c3a1
Revises: a4f8c2d6e1b9
Create Date: 2026-07-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2d5f9c3a1'
down_revision: Union[str, None] = 'a4f8c2d6e1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('attachment_name', sa.String(length=255), nullable=True))
    op.add_column('messages', sa.Column('attachment_content', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'attachment_content')
    op.drop_column('messages', 'attachment_name')
