import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.note import Note
from app.schemas.note import MAX_NOTE_CONTENT_CHARS, MAX_NOTE_TAG_CHARS, MAX_NOTE_TAGS
from tests.conftest import TestingSessionLocal


async def test_note_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/notes")
    assert resp.status_code == 401


async def test_note_crud(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await client.post(
        "/api/v1/notes",
        json={"title": "Recipe", "content": "Pasta with tomato sauce", "tags": ["food", "dinner"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    note = resp.json()
    note_id = note["id"]
    assert note["tags"] == ["food", "dinner"]

    resp = await client.get("/api/v1/notes", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    resp = await client.get(f"/api/v1/notes/{note_id}", headers=auth_headers)
    assert resp.status_code == 200

    resp = await client.put(
        f"/api/v1/notes/{note_id}", json={"content": "Pasta with marinara sauce"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "Pasta with marinara sauce"
    assert resp.json()["title"] == "Recipe"

    resp = await client.delete(f"/api/v1/notes/{note_id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/notes/{note_id}", headers=auth_headers)
    assert resp.status_code == 404


async def test_note_search(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    await client.post(
        "/api/v1/notes", json={"title": "Grocery list", "content": "milk, eggs, bread"}, headers=auth_headers
    )
    await client.post(
        "/api/v1/notes", json={"title": "Meeting notes", "content": "discuss roadmap"}, headers=auth_headers
    )

    resp = await client.get("/api/v1/notes", params={"q": "grocery"}, headers=auth_headers)
    assert resp.status_code == 200
    results = resp.json()["items"]
    assert len(results) == 1
    assert results[0]["title"] == "Grocery list"

    resp = await client.get("/api/v1/notes", params={"q": "roadmap"}, headers=auth_headers)
    results = resp.json()["items"]
    assert len(results) == 1
    assert results[0]["title"] == "Meeting notes"

    resp = await client.get("/api/v1/notes", params={"q": "nonexistent"}, headers=auth_headers)
    assert resp.json()["items"] == []


async def test_note_idor_protection(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "name": "A", "password": "supersecret123"}
    )
    a_login = await client.post(
        "/api/v1/auth/login", data={"username": "a@example.com", "password": "supersecret123"}
    )
    a_headers = {"Authorization": f"Bearer {a_login.json()['access_token']}"}
    create_resp = await client.post("/api/v1/notes", json={"title": "Private", "content": "secret"}, headers=a_headers)
    note_id = create_resp.json()["id"]

    await client.post(
        "/api/v1/auth/register", json={"email": "b@example.com", "name": "B", "password": "supersecret123"}
    )
    b_login = await client.post(
        "/api/v1/auth/login", data={"username": "b@example.com", "password": "supersecret123"}
    )
    b_headers = {"Authorization": f"Bearer {b_login.json()['access_token']}"}

    resp = await client.get(f"/api/v1/notes/{note_id}", headers=b_headers)
    assert resp.status_code == 404

    resp = await client.put(f"/api/v1/notes/{note_id}", json={"content": "hacked"}, headers=b_headers)
    assert resp.status_code == 404

    resp = await client.delete(f"/api/v1/notes/{note_id}", headers=b_headers)
    assert resp.status_code == 404


# --- Bounds and spend guards on the embedding path ---------------------------


async def test_note_content_over_the_cap_is_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Content was unbounded, so the 1 MB body limit was the only ceiling on
    what a single write could hand to the embeddings API."""
    resp = await client.post(
        "/api/v1/notes",
        json={"title": "Huge", "content": "x" * (MAX_NOTE_CONTENT_CHARS + 1)},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/api/v1/notes",
        json={"title": "At the limit", "content": "x" * MAX_NOTE_CONTENT_CHARS},
        headers=auth_headers,
    )
    assert resp.status_code == 201


async def test_note_tags_are_bounded_in_count_and_length(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    too_many = await client.post(
        "/api/v1/notes",
        json={"title": "Tagged", "tags": [f"t{i}" for i in range(MAX_NOTE_TAGS + 1)]},
        headers=auth_headers,
    )
    assert too_many.status_code == 422

    too_long = await client.post(
        "/api/v1/notes",
        json={"title": "Tagged", "tags": ["x" * (MAX_NOTE_TAG_CHARS + 1)]},
        headers=auth_headers,
    )
    assert too_long.status_code == 422


async def test_updating_a_note_is_bounded_too(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The update path embeds as well, so it needs the same ceiling — a cap on
    create alone would just move the abuse to PUT."""
    created = await client.post("/api/v1/notes", json={"title": "Note"}, headers=auth_headers)
    note_id = created.json()["id"]

    resp = await client.put(
        f"/api/v1/notes/{note_id}",
        json={"content": "x" * (MAX_NOTE_CONTENT_CHARS + 1)},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_note_writes_are_rate_limited(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every write is a paid embedding call, so the endpoint needs a per-minute
    ceiling the way the chat endpoint has one."""
    monkeypatch.setattr(settings, "NOTES_WRITE_RATE_LIMIT_PER_MINUTE", 3)

    for i in range(3):
        resp = await client.post("/api/v1/notes", json={"title": f"Note {i}"}, headers=auth_headers)
        assert resp.status_code == 201

    blocked = await client.post("/api/v1/notes", json={"title": "One too many"}, headers=auth_headers)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers

    # Reads are unaffected — the limit is on the spend, not on the resource.
    assert (await client.get("/api/v1/notes", headers=auth_headers)).status_code == 200


async def test_reading_a_note_written_before_the_cap_still_works(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The response model must not inherit the write-side length limits, or
    every note stored before they existed would 500 on read."""
    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    async with TestingSessionLocal() as db:
        db.add(
            Note(
                user_id=me.json()["id"],
                title="Legacy",
                content="x" * (MAX_NOTE_CONTENT_CHARS * 3),
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/notes", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"][0]["content"]) == MAX_NOTE_CONTENT_CHARS * 3
