"""add email_tokens table and users.email_verified_at

Revision ID: c5a3e8b1f042
Revises: b7e2d5f9c3a1
Create Date: 2026-08-17 09:10:00.000000

Existing rows are deliberately left with email_verified_at NULL rather than
backfilled as verified: nobody has proven those addresses, and recording that
they had would be a lie the app then relies on. Verification is advisory, so
nothing breaks for them — they simply see the prompt.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5a3e8b1f042'
down_revision: Union[str, None] = 'b7e2d5f9c3a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'email_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'purpose',
            sa.Enum('password_reset', 'email_verification', name='emailtokenpurpose', native_enum=False, length=20),
            nullable=False,
        ),
        # SHA-256 hex digest of the emailed token; the token itself is never stored.
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_email_tokens_token_hash'), 'email_tokens', ['token_hash'], unique=True)
    op.create_index(op.f('ix_email_tokens_user_id'), 'email_tokens', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_tokens_user_id'), table_name='email_tokens')
    op.drop_index(op.f('ix_email_tokens_token_hash'), table_name='email_tokens')
    op.drop_table('email_tokens')
    op.drop_column('users', 'email_verified_at')
