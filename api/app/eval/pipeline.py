"""Wires the eval harness to the *real* retrieval path.

Seeds the golden corpus into a database and retrieves against it with the same
``note_search.search_notes`` the agent's ``search_notes`` tool calls in
production — so the harness measures the shipping retriever, not a mock.

Retrieval quality depends on the backing store, exactly as it does in prod:

* **SQLite / no OpenAI key** — ``search_notes`` falls back to a keyword scan.
  The harness still runs end to end; treat the numbers as a keyword-RAG baseline.
* **Postgres + pgvector + OpenAI key** — notes are embedded and retrieval is
  true cosine-distance semantic search, including the relevance floor. This is
  the configuration the reported numbers should come from.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.eval.dataset import CORPUS, CorpusNote
from app.models.note import Note
from app.models.user import User
from app.services import note_search

# Top-K notes handed to the generator — matches the agent's search_notes tool.
RETRIEVAL_K = 5


def note_text(note: Note | CorpusNote) -> str:
    """Render a note the way it is fed to the model as context."""
    title = (note.title or "").strip()
    content = (note.content or "").strip()
    return f"{title}\n{content}".strip()


async def seed_corpus(db: AsyncSession) -> User:
    """Create a fresh eval user and load the golden corpus, embeddings included.

    ``refresh_note_embedding`` is a no-op without an embeddings key, so on
    SQLite/keyless this just stores the text and retrieval uses the keyword
    fallback; on Postgres with a key it populates the pgvector column.
    """
    user = User(email="eval@aether.local", name="Eval", password_hash="x")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    for spec in CORPUS:
        note = Note(user_id=user.id, title=spec.title, content=spec.content, tags=list(spec.tags))
        db.add(note)
        await note_search.refresh_note_embedding(db, note)
    await db.commit()
    return user


async def retrieve(db: AsyncSession, user: User, question: str, limit: int = RETRIEVAL_K) -> list[Note]:
    return await note_search.search_notes(db, user, question, limit=limit)
