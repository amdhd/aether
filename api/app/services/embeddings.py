"""Text embeddings for semantic note search.

Uses OpenAI's embeddings API. The whole module degrades gracefully when
OPENAI_API_KEY is unset: `embed_text` returns None, callers skip storing a
vector, and semantic search falls back to a keyword scan. This keeps local dev
and CI (which run on SQLite without a key) fully functional.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings


def embeddings_enabled() -> bool:
    return bool(settings.OPENAI_API_KEY)


@lru_cache
def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _note_text(title: str, content: str) -> str:
    title = (title or "").strip()
    content = (content or "").strip()
    return f"{title}\n\n{content}".strip()


async def embed_text(text: str) -> list[float] | None:
    """Return an embedding vector for `text`, or None if embeddings are disabled
    or the text is empty. Network/API errors are swallowed so a transient
    embedding failure never blocks the underlying note write."""
    if not embeddings_enabled() or not text.strip():
        return None
    try:
        resp = await _client().embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=text,
            dimensions=settings.EMBEDDING_DIMENSIONS,
        )
    except Exception:
        return None
    return resp.data[0].embedding


async def embed_note(title: str, content: str) -> list[float] | None:
    return await embed_text(_note_text(title, content))
