"""add reasoning_content to messages

Revision ID: 8a1c2f4e6b9d
Revises: 572e9eb35d07
Create Date: 2026-06-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a1c2f4e6b9d'
down_revision: Union[str, None] = '572e9eb35d07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('reasoning_content', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'reasoning_content')
