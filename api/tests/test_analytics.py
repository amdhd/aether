from datetime import datetime, timedelta, timezone

from httpx import AsyncClient

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.usage_log import UsageLog
from tests.conftest import TestingSessionLocal


async def test_analytics_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/analytics/summary")
    assert resp.status_code == 401


async def test_analytics_empty_state(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages_per_day"]) == 14
    assert all(day["count"] == 0 for day in data["messages_per_day"])
    assert all(day["prompt_tokens"] == 0 and day["completion_tokens"] == 0 for day in data["tokens_per_day"])
    assert data["tool_usage"] == []
    assert data["totals"] == {
        "conversations": 0,
        "messages": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


async def test_analytics_summary_aggregates_data(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    create_resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = create_resp.json()["id"]

    # find the user id behind the auth headers via the conversation owner
    async with TestingSessionLocal() as session:
        conversation = await session.get(Conversation, conversation_id)
        user_id = conversation.user_id

        now = datetime.now(timezone.utc)
        today = now.replace(hour=12, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)

        session.add_all(
            [
                Message(conversation_id=conversation_id, role=MessageRole.user, content="hi", created_at=today),
                Message(conversation_id=conversation_id, role=MessageRole.user, content="again", created_at=today),
                Message(
                    conversation_id=conversation_id, role=MessageRole.user, content="yesterday", created_at=yesterday
                ),
                Message(
                    conversation_id=conversation_id,
                    role=MessageRole.tool,
                    content="{}",
                    tool_name="get_weather",
                    created_at=today,
                ),
                Message(
                    conversation_id=conversation_id,
                    role=MessageRole.tool,
                    content="{}",
                    tool_name="get_weather",
                    created_at=today,
                ),
                Message(
                    conversation_id=conversation_id,
                    role=MessageRole.tool,
                    content="{}",
                    tool_name="web_search",
                    created_at=today,
                ),
                UsageLog(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    model="deepseek-chat",
                    prompt_tokens=100,
                    completion_tokens=50,
                    created_at=today,
                ),
                UsageLog(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    model="deepseek-chat",
                    prompt_tokens=200,
                    completion_tokens=80,
                    created_at=yesterday,
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/analytics/summary?days=14", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    today_key = today.date().isoformat()
    yesterday_key = yesterday.date().isoformat()

    messages_by_day = {d["date"]: d["count"] for d in data["messages_per_day"]}
    assert messages_by_day[today_key] == 2
    assert messages_by_day[yesterday_key] == 1

    tokens_by_day = {d["date"]: d for d in data["tokens_per_day"]}
    assert tokens_by_day[today_key]["prompt_tokens"] == 100
    assert tokens_by_day[today_key]["completion_tokens"] == 50
    assert tokens_by_day[yesterday_key]["prompt_tokens"] == 200
    assert tokens_by_day[yesterday_key]["completion_tokens"] == 80

    tool_usage = {t["tool_name"]: t["count"] for t in data["tool_usage"]}
    assert tool_usage["get_weather"] == 2
    assert tool_usage["web_search"] == 1

    assert data["totals"] == {
        "conversations": 1,
        "messages": 3,
        "prompt_tokens": 300,
        "completion_tokens": 130,
    }


async def test_analytics_idor_protection(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "name": "A", "password": "supersecret123"}
    )
    a_login = await client.post(
        "/api/v1/auth/login", data={"username": "a@example.com", "password": "supersecret123"}
    )
    a_headers = {"Authorization": f"Bearer {a_login.json()['access_token']}"}

    create_resp = await client.post("/api/v1/conversations", json={}, headers=a_headers)
    conversation_id = create_resp.json()["id"]
    async with TestingSessionLocal() as session:
        session.add(
            Message(conversation_id=conversation_id, role=MessageRole.user, content="secret", created_at=datetime.now(timezone.utc))
        )
        await session.commit()

    await client.post(
        "/api/v1/auth/register", json={"email": "b@example.com", "name": "B", "password": "supersecret123"}
    )
    b_login = await client.post(
        "/api/v1/auth/login", data={"username": "b@example.com", "password": "supersecret123"}
    )
    b_headers = {"Authorization": f"Bearer {b_login.json()['access_token']}"}

    resp = await client.get("/api/v1/analytics/summary", headers=b_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["totals"]["messages"] == 0
    assert all(day["count"] == 0 for day in data["messages_per_day"])
