"""Semantic + keyword search over a user's notes.

On Postgres with embeddings available, notes are ranked by pgvector cosine
distance against the query embedding (true semantic search). Everywhere else —
SQLite tests, or when OPENAI_API_KEY is unset — it transparently falls back to
a case-insensitive keyword scan so the feature still returns sensible results.
"""

import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.note import Note
from app.models.user import User
from app.services import embeddings

# Words too common to be worth matching on in the keyword fallback. Kept tiny —
# just the filler words that would otherwise make every note match a
# natural-language question.
_KEYWORD_STOPWORDS = frozenset(
    "the a an and or of to in on at for is are was were be do does did how what "
    "when where why who which with without from by as into about over under my "
    "your his her our their this that these those it its can could should would "
    "will shall may i you we they me us".split()
)


def _keyword_terms(query: str, limit: int = 12) -> list[str]:
    """Split a query into distinct content words for the keyword fallback.

    The old fallback matched the whole query string as one ``LIKE '%...%'``
    pattern, so a natural-language question ("how many eggs for carbonara?")
    matched nothing — the literal sentence never appears in a note. Matching on
    individual terms instead recovers the notes that mention any of them.
    """
    seen: dict[str, None] = {}
    for token in re.findall(r"[a-z0-9]+", query.lower()):
        if len(token) > 2 and token not in _KEYWORD_STOPWORDS:
            seen.setdefault(token, None)
    return list(seen)[:limit]


async def refresh_note_embedding(db: AsyncSession, note: Note) -> None:
    """(Re)compute and store a note's embedding. No-op when embeddings are
    disabled. Caller is responsible for committing."""
    vector = await embeddings.embed_note(note.title, note.content)
    if vector is not None:
        note.embedding = vector


def _is_postgres(db: AsyncSession) -> bool:
    bind = db.get_bind()
    return bind.dialect.name == "postgresql"


async def _keyword_search(db: AsyncSession, user: User, query: str, limit: int) -> list[Note]:
    terms = _keyword_terms(query)
    # No usable terms (query was all stopwords/punctuation): fall back to the
    # whole-string match so a query like "C#" or a stopword phrase still runs.
    patterns = [f"%{t}%" for t in terms] if terms else [f"%{query.strip()}%"]
    conditions = [or_(Note.title.ilike(p), Note.content.ilike(p)) for p in patterns]
    stmt = (
        select(Note)
        .where(Note.user_id == user.id)
        .where(or_(*conditions))
        .order_by(Note.updated_at.desc())
        .limit(limit)
    )
    return list((await db.scalars(stmt)).all())


async def search_notes(db: AsyncSession, user: User, query: str, limit: int = 5) -> list[Note]:
    if _is_postgres(db) and embeddings.embeddings_enabled():
        query_vector = await embeddings.embed_text(query)
        if query_vector is not None:
            # Drop notes past the relevance floor so the agent isn't handed
            # near-random matches when nothing in the user's notes is actually
            # about the query; if that leaves nothing, fall back to keywords.
            distance = Note.embedding.cosine_distance(query_vector)
            stmt = (
                select(Note)
                .where(Note.user_id == user.id, Note.embedding.is_not(None))
                .where(distance <= settings.NOTE_SEARCH_MAX_DISTANCE)
                .order_by(distance)
                .limit(limit)
            )
            results = list((await db.scalars(stmt)).all())
            if results:
                return results
    return await _keyword_search(db, user, query, limit)
