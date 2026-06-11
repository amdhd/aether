from httpx import AsyncClient


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
    assert len(resp.json()) == 1

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
    results = resp.json()
    assert len(results) == 1
    assert results[0]["title"] == "Grocery list"

    resp = await client.get("/api/v1/notes", params={"q": "roadmap"}, headers=auth_headers)
    assert len(resp.json()) == 1
    assert resp.json()[0]["title"] == "Meeting notes"

    resp = await client.get("/api/v1/notes", params={"q": "nonexistent"}, headers=auth_headers)
    assert resp.json() == []


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
