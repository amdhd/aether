"""add notes.embedding column (pgvector)

Revision ID: a4f8c2d6e1b9
Revises: 9b3d1f7a2c4e
Create Date: 2026-06-21 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.config import settings


# revision identifiers, used by Alembic.
revision: str = 'a4f8c2d6e1b9'
down_revision: Union[str, None] = '9b3d1f7a2c4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # Real semantic search path: enable pgvector and use a native vector
        # column so cosine-distance queries can be index-accelerated.
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')
        from pgvector.sqlalchemy import Vector

        op.add_column('notes', sa.Column('embedding', Vector(settings.EMBEDDING_DIMENSIONS), nullable=True))
    else:
        # SQLite (local dev / CI): store the vector as JSON so the schema builds
        # without the extension; search falls back to a keyword scan.
        op.add_column('notes', sa.Column('embedding', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('notes', 'embedding')
