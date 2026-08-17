import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.note import Note
from app.models.usage_log import UsageLog
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
    # embed_note now reports the tokens it billed alongside the vector, so the
    # caller can charge them to the user.
    monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)
    monkeypatch.setattr(note_search.embeddings, "embed_note", lambda *a, **k: _async((vector, 7)))
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
        monkeypatch.setattr(
            note_search.embeddings, "embed_text_with_usage", lambda _t: _async(([0.1] * 8, 3))
        )

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


# --- Embedding spend is charged to the user ----------------------------------


async def _month_to_date(db, user_id: int) -> float:
    from app.core.cost_cap import month_to_date_cost_usd

    return await month_to_date_cost_usd(db, user_id)


async def test_note_embedding_is_charged_to_the_users_monthly_spend(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    """The monthly cap only summed chat tokens, so embedding spend — which
    every note write incurs — was invisible to the one control bounding what an
    account can cost over a month."""
    vector = [0.1] * embeddings.settings.EMBEDDING_DIMENSIONS
    monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)
    monkeypatch.setattr(note_search.embeddings, "embed_note", lambda *a, **k: _async((vector, 1_000_000)))

    async with TestingSessionLocal() as db:
        assert await _month_to_date(db, user.id) == 0.0

        note = Note(user_id=user.id, title="Recipe", content="pasta")
        db.add(note)
        await note_search.refresh_note_embedding(db, note)
        await db.commit()

        # One million embedding tokens, priced at the embedding rate — not the
        # much higher chat input rate, which is why it needs its own column.
        spent = await _month_to_date(db, user.id)
        assert spent == pytest.approx(embeddings.settings.EMBEDDING_COST_PER_1M_TOKENS)


async def test_embedding_is_skipped_but_the_note_still_saves_when_over_budget(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    """Over budget, semantic search degrades to the keyword scan that already
    covers every keyless deployment. Refusing to save the note would not be an
    acceptable way to enforce a spend cap."""
    monkeypatch.setattr(note_search.settings, "MONTHLY_COST_CAP_USD", 0.001)
    monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)

    called = {"n": 0}

    async def _should_not_run(*args: object, **kwargs: object):
        called["n"] += 1
        return ([0.1] * 8, 5)

    monkeypatch.setattr(note_search.embeddings, "embed_note", _should_not_run)

    async with TestingSessionLocal() as db:
        # Put the user well past the cap.
        db.add(UsageLog(user_id=user.id, model="test", prompt_tokens=10_000_000))
        await db.commit()

        note = Note(user_id=user.id, title="Recipe", content="pasta")
        db.add(note)
        await note_search.refresh_note_embedding(db, note)
        await db.commit()
        await db.refresh(note)

    assert called["n"] == 0, "no embedding call should be made once over budget"
    assert note.embedding is None
    assert note.id is not None, "the note itself must still be saved"


async def test_a_usage_log_row_needs_no_conversation(user: User) -> None:
    """Note embeddings have no chat turn to attribute themselves to, so
    conversation_id has to accept NULL."""
    async with TestingSessionLocal() as db:
        db.add(UsageLog(user_id=user.id, model="text-embedding-3-small", embedding_tokens=42))
        await db.commit()

        total = await db.scalar(
            select(func.coalesce(func.sum(UsageLog.embedding_tokens), 0)).where(
                UsageLog.user_id == user.id
            )
        )
    assert total == 42


async def test_oversized_embedding_input_is_truncated_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the model's context window the API rejects the call outright, and
    the except-clause would turn that into a silent 'no embedding' — semantic
    search failing quietly on exactly the longest notes."""
    monkeypatch.setattr(embeddings.settings, "OPENAI_API_KEY", "test-key")
    seen: dict[str, int] = {}

    class _Embeddings:
        async def create(self, **kwargs):
            seen["chars"] = len(kwargs["input"])
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1] * 8)],
                usage=SimpleNamespace(total_tokens=11),
            )

    monkeypatch.setattr(embeddings, "_client", lambda: SimpleNamespace(embeddings=_Embeddings()))

    vector, tokens = await embeddings.embed_text_with_usage(
        "x" * (embeddings.MAX_EMBEDDING_INPUT_CHARS + 5_000)
    )
    assert vector is not None
    assert tokens == 11
    assert seen["chars"] == embeddings.MAX_EMBEDDING_INPUT_CHARS


async def test_a_failed_embedding_bills_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings.settings, "OPENAI_API_KEY", "test-key")

    class _BoomEmbeddings:
        async def create(self, **kwargs):
            raise RuntimeError("upstream is down")

    monkeypatch.setattr(embeddings, "_client", lambda: SimpleNamespace(embeddings=_BoomEmbeddings()))

    vector, tokens = await embeddings.embed_text_with_usage("hello")
    assert vector is None
    assert tokens == 0
