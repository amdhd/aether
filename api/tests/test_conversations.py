from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.core.config import settings


def _content_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, reasoning_content=None, tool_calls=None))],
        usage=None,
    )


def _reasoning_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content=text, tool_calls=None))],
        usage=None,
    )


def _usage_chunk(prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _tool_call_chunk(index: int, call_id: str, name: str, arguments: str) -> SimpleNamespace:
    function = SimpleNamespace(name=name, arguments=arguments)
    tool_call = SimpleNamespace(index=index, id=call_id, function=function)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tool_call]))],
        usage=None,
    )


class _FakeStream:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _FakeCompletions:
    def __init__(self, responses: list[list[SimpleNamespace]]) -> None:
        self._responses = responses
        self.call_count = 0

    async def create(self, **kwargs):
        chunks = self._responses[self.call_count]
        self.call_count += 1
        return _FakeStream(chunks)


class _FakeClient:
    def __init__(self, responses: list[list[SimpleNamespace]]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _patch_deepseek(monkeypatch: pytest.MonkeyPatch, responses: list[list[SimpleNamespace]]) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("app.agent.loop.get_deepseek_client", lambda: _FakeClient(responses))


async def test_conversation_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/conversations")
    assert resp.status_code == 401


async def test_conversation_crud(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await client.post(
        "/api/v1/conversations", json={"title": "Trip planning", "persona": "research_assistant"}, headers=auth_headers
    )
    assert resp.status_code == 201
    conversation = resp.json()
    conversation_id = conversation["id"]
    assert conversation["title"] == "Trip planning"
    assert conversation["persona"] == "research_assistant"

    resp = await client.get("/api/v1/conversations", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["messages"] == []

    resp = await client.put(
        f"/api/v1/conversations/{conversation_id}", json={"title": "Renamed"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"

    resp = await client.delete(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    assert resp.status_code == 404


async def test_conversation_idor_protection(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "name": "A", "password": "supersecret123"}
    )
    a_login = await client.post(
        "/api/v1/auth/login", data={"username": "a@example.com", "password": "supersecret123"}
    )
    a_headers = {"Authorization": f"Bearer {a_login.json()['access_token']}"}
    create_resp = await client.post("/api/v1/conversations", json={}, headers=a_headers)
    conversation_id = create_resp.json()["id"]

    await client.post(
        "/api/v1/auth/register", json={"email": "b@example.com", "name": "B", "password": "supersecret123"}
    )
    b_login = await client.post(
        "/api/v1/auth/login", data={"username": "b@example.com", "password": "supersecret123"}
    )
    b_headers = {"Authorization": f"Bearer {b_login.json()['access_token']}"}

    resp = await client.get(f"/api/v1/conversations/{conversation_id}", headers=b_headers)
    assert resp.status_code == 404


async def test_chat_message_requires_deepseek_key(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    create_resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = create_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Hi"}, headers=auth_headers
    )
    assert resp.status_code == 503


async def test_chat_message_simple_response(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_deepseek(
        monkeypatch,
        responses=[[_content_chunk("Hello"), _content_chunk(" there!"), _usage_chunk(10, 5)]],
    )

    create_resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = create_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Hi"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: token" in body
    assert '"content": "Hello"' in body
    assert "event: done" in body

    detail = await client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    messages = detail.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Hello there!"

    convo = detail.json()
    assert convo["title"] == "Hi"


async def test_chat_message_rate_limit(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MINUTE", 2)

    create_resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = create_resp.json()["id"]

    for _ in range(2):
        resp = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Hi"}, headers=auth_headers
        )
        assert resp.status_code == 503  # DEEPSEEK_API_KEY not configured, but rate limit not yet hit

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Hi"}, headers=auth_headers
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


async def test_chat_message_with_reasoning(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_deepseek(
        monkeypatch,
        responses=[
            [
                _reasoning_chunk("Let me think..."),
                _content_chunk("Hello"),
                _content_chunk(" there!"),
                _usage_chunk(10, 5),
            ]
        ],
    )

    create_resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = create_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Hi"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: reasoning" in body
    assert '"content": "Let me think..."' in body
    assert "event: token" in body

    detail = await client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    messages = detail.json()["messages"]
    assistant_msg = messages[1]
    assert assistant_msg["content"] == "Hello there!"
    assert assistant_msg["reasoning_content"] == "Let me think..."


async def test_chat_message_with_tool_call(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_deepseek(
        monkeypatch,
        responses=[
            [
                _tool_call_chunk(0, "call_1", "create_task", '{"title": "Buy milk"}'),
                _usage_chunk(20, 8),
            ],
            [_content_chunk("Done, I added that task."), _usage_chunk(30, 12)],
        ],
    )

    create_resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = create_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Add a task to buy milk"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: tool_call" in body
    assert '"name": "create_task"' in body
    assert "event: done" in body

    tasks_resp = await client.get("/api/v1/tasks", headers=auth_headers)
    tasks = tasks_resp.json()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Buy milk"

    detail = await client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    roles = [m["role"] for m in detail.json()["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
