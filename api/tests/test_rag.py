import logging
from types import SimpleNamespace

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


async def test_embed_text_logs_and_degrades_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A configured-but-failing embeddings API (e.g. a bad/expired key) must not
    # blow up the caller: embed_text returns None so search degrades to keyword
    # scan, but the failure is logged so it isn't invisible.
    monkeypatch.setattr(embeddings.settings, "OPENAI_API_KEY", "test-key")

    class _BoomEmbeddings:
        async def create(self, **kwargs):
            raise RuntimeError("upstream 500")

    monkeypatch.setattr(embeddings, "_client", lambda: SimpleNamespace(embeddings=_BoomEmbeddings()))

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logging.getLogger("app").addHandler(handler)
    try:
        result = await embeddings.embed_text("hello")
    finally:
        logging.getLogger("app").removeHandler(handler)

    assert result is None
    assert any("embedding.failed" in r.getMessage() for r in records)


async def test_refresh_note_embedding_stores_vector(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    # Match the configured dimension so this works against the native
    # Vector(EMBEDDING_DIMENSIONS) column on Postgres, not just SQLite's JSON.
    vector = [0.1] * embeddings.settings.EMBEDDING_DIMENSIONS
    monkeypatch.setattr(note_search.embeddings, "embed_note", lambda *a, **k: _async(vector))
    async with TestingSessionLocal() as db:
        note = Note(user_id=user.id, title="Recipe", content="pasta and basil")
        db.add(note)
        await note_search.refresh_note_embedding(db, note)
        await db.commit()
        await db.refresh(note)
        # pgvector round-trips as a numpy array on Postgres, a list on SQLite;
        # normalize to a list before comparing.
        assert list(note.embedding) == vector


async def test_search_falls_back_to_keyword(user: User) -> None:
    # On SQLite (no pgvector), search_notes must still return keyword matches.
    async with TestingSessionLocal() as db:
        db.add(Note(user_id=user.id, title="Travel plan", content="flights to Tokyo"))
        db.add(Note(user_id=user.id, title="Groceries", content="milk and eggs"))
        await db.commit()
        results = await note_search.search_notes(db, user, "Tokyo", limit=5)
        assert [n.title for n in results] == ["Travel plan"]


async def test_keyword_search_matches_natural_language_question(user: User) -> None:
    # The keyword fallback tokenizes the query, so a full-sentence question
    # matches notes that mention any of its content words — the old whole-string
    # LIKE matched nothing because the literal sentence never appears in a note.
    async with TestingSessionLocal() as db:
        db.add(Note(user_id=user.id, title="Carbonara recipe", content="2 eggs plus 1 yolk"))
        db.add(Note(user_id=user.id, title="Groceries", content="milk and bread"))
        await db.commit()
        results = await note_search.search_notes(
            db, user, "How many eggs do I use for carbonara?", limit=5
        )
        assert [n.title for n in results] == ["Carbonara recipe"]


async def test_keyword_search_ignores_stopword_only_query(user: User) -> None:
    # A query of nothing but stopwords has no usable terms; it must not match
    # every note via an empty pattern.
    async with TestingSessionLocal() as db:
        db.add(Note(user_id=user.id, title="Anything", content="some content"))
        await db.commit()
        results = await note_search.search_notes(db, user, "how do i", limit=5)
        assert results == []


async def test_semantic_below_relevance_floor_falls_back_to_keyword(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When every note is past the relevance floor, the semantic query returns
    # nothing and search must fall back to the keyword scan rather than return
    # empty. We simulate the (Postgres-only) semantic path returning no rows.
    async with TestingSessionLocal() as db:
        db.add(Note(user_id=user.id, title="Travel plan", content="flights to Tokyo"))
        await db.commit()

        monkeypatch.setattr(note_search, "_is_postgres", lambda _db: True)
        monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)
        monkeypatch.setattr(note_search.embeddings, "embed_text", lambda _t: _async([0.1] * 8))

        real_scalars = db.scalars
        calls = {"n": 0}

        async def fake_scalars(stmt):
            calls["n"] += 1
            if calls["n"] == 1:
                # Semantic query: everything filtered out by the distance floor.
                return SimpleNamespace(all=lambda: [])
            return await real_scalars(stmt)

        monkeypatch.setattr(db, "scalars", fake_scalars)

        results = await note_search.search_notes(db, user, "Tokyo", limit=5)
        assert [n.title for n in results] == ["Travel plan"]
        assert calls["n"] == 2  # semantic attempted, then keyword fallback ran


def _async(value):
    async def _coro():
        return value

    return _coro()
