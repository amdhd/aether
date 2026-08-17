"""Text embeddings for semantic note search.

Uses OpenAI's embeddings API. The whole module degrades gracefully when
OPENAI_API_KEY is unset: `embed_text` returns None, callers skip storing a
vector, and semantic search falls back to a keyword scan. This keeps local dev
and CI (which run on SQLite without a key) fully functional.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from app.core import usage
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def embeddings_enabled() -> bool:
    return bool(settings.OPENAI_API_KEY)


@lru_cache
def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# The embedding model rejects input past its context window outright, and
# embed_text's except-clause would turn that rejection into a silent "no
# embedding" — semantic search quietly failing on exactly the longest notes.
# Note bodies are capped well under this at the schema (MAX_NOTE_CONTENT_CHARS),
# so this only catches text that reached us another way: a note written before
# that cap existed, or a long search query. ~4 chars/token against the model's
# 8191-token limit, with headroom for text that tokenizes worse than average.
MAX_EMBEDDING_INPUT_CHARS = 28_000


def _note_text(title: str, content: str) -> str:
    title = (title or "").strip()
    content = (content or "").strip()
    return f"{title}\n\n{content}".strip()


async def embed_text_with_usage(text: str) -> tuple[list[float] | None, int]:
    """Embed `text`, returning ``(vector, tokens_billed)``.

    ``tokens_billed`` is 0 whenever no call was made or the call failed, so a
    caller can record spend without having to guess whether it was incurred.
    Network/API errors are swallowed so a transient embedding failure never
    blocks the underlying note write.
    """
    if not embeddings_enabled() or not text.strip():
        return None, 0
    if len(text) > MAX_EMBEDDING_INPUT_CHARS:
        logger.warning(
            "embedding.truncated chars=%d limit=%d", len(text), MAX_EMBEDDING_INPUT_CHARS
        )
        text = text[:MAX_EMBEDDING_INPUT_CHARS]
    try:
        resp = await _client().embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=text,
            dimensions=settings.EMBEDDING_DIMENSIONS,
        )
    except Exception as exc:
        # Degrade gracefully (search falls back to keyword scan), but leave a
        # trace: without this, a bad/expired OPENAI_API_KEY silently disables
        # semantic search indefinitely with no signal that anything is wrong.
        logger.warning("embedding.failed error=%r", exc)
        return None, 0
    tokens = getattr(resp.usage, "total_tokens", 0) or 0
    # No-op unless a caller opened a usage meter. This is the only place query
    # and note embeddings are billed, so metering here is what lets the eval
    # harness attribute retrieval's embedding cost to the sample that caused it.
    usage.record_embedding(tokens)
    return resp.data[0].embedding, tokens


async def embed_text(text: str) -> list[float] | None:
    """Vector only, for callers with nothing to charge the tokens to."""
    vector, _ = await embed_text_with_usage(text)
    return vector


async def embed_note(title: str, content: str) -> tuple[list[float] | None, int]:
    return await embed_text_with_usage(_note_text(title, content))
