"""Single-use claims on client-supplied idempotency keys.

The write this guards — sending a chat message — streams SSE for the length of a
model turn, and a mid-turn network drop is exactly when a client retries. That
retry re-bills a whole LLM turn and appends a second copy of the user's message,
and because history is replayed verbatim the duplicate is visible forever after.

A streamed response cannot be stored and replayed, so a duplicate is refused
rather than reproduced. Refusing only needs to know the key was already spent,
which is all this stores.

The claim is an INSERT against a unique constraint, not a SELECT-then-INSERT:
two requests arriving together is the case that matters, and only the database
can decide that race. This is the same reasoning as the conditional UPDATE in
`services.refresh_tokens`.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_key import IdempotencyKey
from app.models.user import User

# Long enough for a UUID or any reasonable opaque token, bounded because it
# reaches a column and an index.
MAX_KEY_LENGTH = 255


async def claim(db: AsyncSession, user: User, scope: str, key: str) -> bool:
    """Spend `key` for this user and scope. False means it was already spent.

    Commits, because the claim has to be visible to a concurrent request
    immediately — an uncommitted claim is invisible to the very request it
    exists to refuse.
    """
    db.add(IdempotencyKey(user_id=user.id, scope=scope, key=key))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True
