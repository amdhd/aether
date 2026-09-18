"""merge email token and embedding token branches

Empty on purpose: a merge revision carries no DDL. It exists to give the two
branches a single descendant so `alembic upgrade head` resolves again.

Both parents were written against b7e2d5f9c3a1 in parallel pull requests and
merged independently, which left the history with two heads. Nothing in the
schema conflicts — one adds email_tokens, the other a usage_logs column — but
`upgrade head` refuses to pick between heads, so every deploy path that runs it
failed before touching the database.

Revision ID: 7fe369e229f1
Revises: c5a3e8b1f042, e3c8b5d2a7f1
Create Date: 2026-09-18 19:27:40.530255

"""
from typing import Sequence, Union

# No `op` / `sa` import: the generated template brings them in, but a merge
# revision emits no DDL and ruff fails the build on the unused imports.


# revision identifiers, used by Alembic.
revision: str = '7fe369e229f1'
down_revision: Union[str, None] = ('c5a3e8b1f042', 'e3c8b5d2a7f1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
