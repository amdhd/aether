"""Real pgvector semantic-search tests.

These only run when the suite is pointed at Postgres (TEST_DATABASE_URL), where
notes.embedding is a native Vector column and cosine_distance is executed by the
database. On SQLite the whole module is skipped. This is the only place the
relevance-floor and distance-ordering logic in note_search runs against a real
vector index rather than the keyword fallback.
"""

import pytest

from app.core.config import settings
from app.models.note import Note
from app.models.user import User
from app.services import note_search
from tests import conftest
from tests.conftest import TestingSessionLocal

pytestmark = pytest.mark.skipif(conftest.IS_SQLITE, reason="requires Postgres + pgvector")

_D = settings.EMBEDDING_DIMENSIONS


def _vec(*head: float) -> list[float]:
    """A unit-length D-dimensional vector with the given leading components."""
    vector = [0.0] * _D
    for i, value in enumerate(head):
        vector[i] = value
    return vector


async def _make_user(email: str) -> User:
    async with TestingSessionLocal() as db:
        user = User(email=email, name="Pg", password_hash="x")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def test_semantic_search_orders_by_distance_and_applies_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _make_user("pgvec@example.com")

    query = _vec(1.0)  # axis 0
    near = _vec(1.0)  # identical -> cosine distance 0
    mid = _vec(0.8, 0.6)  # cosine distance 0.2 (within the 0.6 floor)
    far = _vec(0.0, 1.0)  # orthogonal -> cosine distance 1.0 (beyond the floor)

    async with TestingSessionLocal() as db:
        db.add(Note(user_id=user.id, title="Near", content="n", embedding=near))
        db.add(Note(user_id=user.id, title="Mid", content="m", embedding=mid))
        db.add(Note(user_id=user.id, title="Far", content="f", embedding=far))
        await db.commit()

        monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)
        monkeypatch.setattr(note_search.embeddings, "embed_text", lambda _t: _async(query))

        results = await note_search.search_notes(db, user, "unused", limit=5)

    # "Far" is dropped by the relevance floor; the rest come back nearest-first.
    assert [n.title for n in results] == ["Near", "Mid"]


async def test_semantic_all_beyond_floor_falls_back_to_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _make_user("pgvec2@example.com")

    query = _vec(1.0)
    orthogonal = _vec(0.0, 1.0)  # distance 1.0 -> beyond the floor for every note

    async with TestingSessionLocal() as db:
        db.add(Note(user_id=user.id, title="Zebra facts", content="zebras roam", embedding=orthogonal))
        await db.commit()

        monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)
        monkeypatch.setattr(note_search.embeddings, "embed_text", lambda _t: _async(query))

        # Nothing passes the vector floor, so the keyword scan takes over and
        # still finds the note by its text.
        results = await note_search.search_notes(db, user, "zebra", limit=5)

    assert [n.title for n in results] == ["Zebra facts"]


def _async(value):
    async def _coro():
        return value

    return _coro()
