"""index usage_logs on (user_id, created_at)

The month-to-date spend check runs before every chat turn:
`WHERE user_id = ? AND created_at >= ?`. With only a user_id index Postgres
finds the user's rows and reads all of them to filter by date, so the per-turn
cost grows with that user's whole history. The analytics day-buckets run the
same shape.

The old user_id-only index is dropped rather than kept: this index leads with
user_id, so it answers a user_id-only lookup and the cascade delete too, and
keeping both costs two index writes per row to answer one query.

Revision ID: 22d488ef009b
Revises: 7fe369e229f1
Create Date: 2026-09-20 20:55:08.596118

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '22d488ef009b'
down_revision: Union[str, None] = '7fe369e229f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_usage_logs_user_id_created_at", "usage_logs", ["user_id", "created_at"], unique=False
    )
    op.drop_index(op.f("ix_usage_logs_user_id"), table_name="usage_logs")


def downgrade() -> None:
    op.create_index(op.f("ix_usage_logs_user_id"), "usage_logs", ["user_id"], unique=False)
    op.drop_index("ix_usage_logs_user_id_created_at", table_name="usage_logs")
