"""add conversation memory_summarized_until_id

Revision ID: 3f6d9a1c7e2b
Revises: 8a1c2f4e6b9d
Create Date: 2026-06-11 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f6d9a1c7e2b'
down_revision: Union[str, None] = '8a1c2f4e6b9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('memory_summarized_until_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'memory_summarized_until_id')
