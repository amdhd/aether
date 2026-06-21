import pytest

from app.models.note import Note
from app.models.user import User
from app.services import embeddings, note_search
from tests.conftest import TestingSessionLocal


@pytest.fixture
async def user() -> User:
    async with TestingSessionLocal() as db:
        user = User(email="rag@example.com", name="Rag", password_hash="x")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def test_embed_text_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings.settings, "OPENAI_API_KEY", "")
    assert await embeddings.embed_text("hello") is None


async def test_refresh_note_embedding_stores_vector(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    vector = [0.1] * 8
    monkeypatch.setattr(note_search.embeddings, "embed_note", lambda *a, **k: _async(vector))
    async with TestingSessionLocal() as db:
        note = Note(user_id=user.id, title="Recipe", content="pasta and basil")
        db.add(note)
        await note_search.refresh_note_embedding(db, note)
        await db.commit()
        await db.refresh(note)
        assert note.embedding == vector


async def test_search_falls_back_to_keyword(user: User) -> None:
    # On SQLite (no pgvector), search_notes must still return keyword matches.
    async with TestingSessionLocal() as db:
        db.add(Note(user_id=user.id, title="Travel plan", content="flights to Tokyo"))
        db.add(Note(user_id=user.id, title="Groceries", content="milk and eggs"))
        await db.commit()
        results = await note_search.search_notes(db, user, "Tokyo", limit=5)
        assert [n.title for n in results] == ["Travel plan"]


def _async(value):
    async def _coro():
        return value

    return _coro()
