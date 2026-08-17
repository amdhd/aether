"""add usage_logs.embedding_tokens and allow a null conversation_id

Embedding spend was invisible to the monthly cost cap, which sums this table.
Embeddings are priced differently from chat tokens, so they get their own
column rather than being folded into prompt_tokens; and a note embedding has no
conversation to attribute itself to, so conversation_id becomes nullable.

Revision ID: e3c8b5d2a7f1
Revises: b7e2d5f9c3a1
Create Date: 2026-08-17 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3c8b5d2a7f1'
down_revision: Union[str, None] = 'b7e2d5f9c3a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode so this also applies on SQLite, which cannot ALTER a column's
    # nullability in place and needs the table rebuilt.
    with op.batch_alter_table('usage_logs') as batch_op:
        batch_op.add_column(
            sa.Column('embedding_tokens', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.alter_column('conversation_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Rows recording embedding spend have no conversation, so they cannot
    # survive conversation_id going back to NOT NULL. Drop them rather than
    # letting the ALTER fail on a populated table.
    op.execute('DELETE FROM usage_logs WHERE conversation_id IS NULL')
    with op.batch_alter_table('usage_logs') as batch_op:
        batch_op.alter_column('conversation_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('embedding_tokens')
