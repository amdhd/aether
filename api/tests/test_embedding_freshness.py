import pytest

from app.core.security import decode_token
from app.models.note import Note
from app.services import note_search
from tests.conftest import TestingSessionLocal


@pytest.mark.parametrize("failure", ["disabled", "budget", "provider"])
async def test_edit_clears_stale_vector_and_preserves_new_text(client, auth_headers, monkeypatch, failure):
    user_id = int(decode_token(auth_headers["Authorization"].split()[1])["sub"])
    old_vector = [0.1] * note_search.settings.EMBEDDING_DIMENSIONS
    async with TestingSessionLocal() as db:
        note = Note(user_id=user_id, title="Old", content="Old meaning", embedding=old_vector)
        db.add(note)
        await db.commit()
        note_id = note.id
    monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: failure != "disabled")

    async def budget(*_args):
        return failure != "budget"

    async def failed_provider(*_args):
        assert failure == "provider"
        return None, 0

    monkeypatch.setattr(note_search, "_within_budget", budget)
    monkeypatch.setattr(note_search.embeddings, "embed_note", failed_provider)
    response = await client.put(f"/api/v1/notes/{note_id}", headers=auth_headers,
                                json={"title": "New", "content": "New meaning"})
    assert response.status_code == 200
    async with TestingSessionLocal() as db:
        note = await db.get(Note, note_id)
        assert note.content == "New meaning"
        assert note.embedding is None


async def test_noop_edit_keeps_vector_but_missing_vector_can_be_retried(client, auth_headers, monkeypatch):
    user_id = int(decode_token(auth_headers["Authorization"].split()[1])["sub"])
    vector = [0.0] * note_search.settings.EMBEDDING_DIMENSIONS
    vector[0] = 1.0
    async with TestingSessionLocal() as db:
        note = Note(user_id=user_id, title="Title", content="Text", embedding=vector)
        db.add(note)
        await db.commit()
        note_id = note.id
    calls = []

    async def embed(title, content):
        calls.append((title, content))
        return vector, 4

    monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)
    monkeypatch.setattr(note_search.embeddings, "embed_note", embed)
    for change in ({"title": "Title", "content": "Text"}, {"tags": ["updated"]}):
        response = await client.put(f"/api/v1/notes/{note_id}", headers=auth_headers, json=change)
        assert response.status_code == 200
    assert calls == []
    async with TestingSessionLocal() as db:
        note = await db.get(Note, note_id)
        assert list(note.embedding) == vector
        note.embedding = None
        await db.commit()
    response = await client.put(f"/api/v1/notes/{note_id}", headers=auth_headers, json={"content": "Text"})
    assert response.status_code == 200
    assert calls == [("Title", "Text")]
    async with TestingSessionLocal() as db:
        assert list((await db.get(Note, note_id)).embedding) == vector


async def test_invalidation_is_written_when_session_originally_saw_null(client, auth_headers, monkeypatch):
    """A stale ORM snapshot must not omit NULL while another writer sets a vector."""
    from sqlalchemy import update

    user_id = int(decode_token(auth_headers["Authorization"].split()[1])["sub"])
    monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: False)
    async with TestingSessionLocal() as db:
        note = Note(user_id=user_id, title="Original", content="Original")
        db.add(note)
        await db.commit()
        # Simulate an intervening write without refreshing the ORM snapshot.
        await db.execute(update(Note).where(Note.id == note.id).values(
            embedding=[0.1] * note_search.settings.EMBEDDING_DIMENSIONS,
        ).execution_options(synchronize_session=False))
        await db.commit()
        assert note.embedding is None
        note.content = "Different meaning"
        await note_search.refresh_note_embedding(db, note)
        await db.commit()
        await db.refresh(note)
        assert note.embedding is None
        assert note.content == "Different meaning"


async def test_successful_edit_replaces_old_vector(client, auth_headers, monkeypatch):
    user_id = int(decode_token(auth_headers["Authorization"].split()[1])["sub"])
    old = [0.0] * note_search.settings.EMBEDDING_DIMENSIONS
    new = old.copy()
    old[0], new[1] = 1.0, 1.0
    async with TestingSessionLocal() as db:
        note = Note(user_id=user_id, title="Old", content="Old", embedding=old)
        db.add(note)
        await db.commit()
        note_id = note.id

    async def embed(title, content):
        assert (title, content) == ("New", "New")
        return new, 4

    monkeypatch.setattr(note_search.embeddings, "embeddings_enabled", lambda: True)
    monkeypatch.setattr(note_search.embeddings, "embed_note", embed)
    response = await client.put(f"/api/v1/notes/{note_id}", headers=auth_headers,
                                json={"title": "New", "content": "New"})
    assert response.status_code == 200
    async with TestingSessionLocal() as db:
        note = await db.get(Note, note_id)
        assert list(note.embedding) == new
        assert note.content == "New"
