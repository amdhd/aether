from httpx import AsyncClient


async def test_task_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 401


async def test_task_crud(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await client.post("/api/v1/tasks", json={"title": "Buy milk"}, headers=auth_headers)
    assert resp.status_code == 201
    task = resp.json()
    task_id = task["id"]
    assert task["status"] == "todo"
    assert task["priority"] == "medium"

    resp = await client.get("/api/v1/tasks", headers=auth_headers)
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] == 1
    assert len(page["items"]) == 1

    resp = await client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers)
    assert resp.status_code == 200

    resp = await client.put(
        f"/api/v1/tasks/{task_id}", json={"status": "doing", "priority": "high"}, headers=auth_headers
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["status"] == "doing"
    assert updated["priority"] == "high"
    assert updated["title"] == "Buy milk"

    resp = await client.delete(f"/api/v1/tasks/{task_id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers)
    assert resp.status_code == 404


async def test_task_validation(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await client.post("/api/v1/tasks", json={"title": ""}, headers=auth_headers)
    assert resp.status_code == 422


async def test_task_idor_protection(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "name": "A", "password": "supersecret123"}
    )
    a_login = await client.post(
        "/api/v1/auth/login", data={"username": "a@example.com", "password": "supersecret123"}
    )
    a_headers = {"Authorization": f"Bearer {a_login.json()['access_token']}"}
    create_resp = await client.post("/api/v1/tasks", json={"title": "Secret task"}, headers=a_headers)
    task_id = create_resp.json()["id"]

    await client.post(
        "/api/v1/auth/register", json={"email": "b@example.com", "name": "B", "password": "supersecret123"}
    )
    b_login = await client.post(
        "/api/v1/auth/login", data={"username": "b@example.com", "password": "supersecret123"}
    )
    b_headers = {"Authorization": f"Bearer {b_login.json()['access_token']}"}

    resp = await client.get(f"/api/v1/tasks/{task_id}", headers=b_headers)
    assert resp.status_code == 404

    resp = await client.put(f"/api/v1/tasks/{task_id}", json={"status": "done"}, headers=b_headers)
    assert resp.status_code == 404

    resp = await client.delete(f"/api/v1/tasks/{task_id}", headers=b_headers)
    assert resp.status_code == 404

    resp = await client.get("/api/v1/tasks", headers=b_headers)
    assert resp.status_code == 200
    assert resp.json()["items"] == []
