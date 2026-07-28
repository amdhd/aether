from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.cost_cap import month_to_date_cost_usd
from app.models.conversation import Conversation
from app.models.usage_log import UsageLog
from app.models.user import User

from .conftest import TestingSessionLocal


async def _seed_usage(prompt_tokens: int, completion_tokens: int, created_at: datetime | None = None) -> None:
    """Attach a usage row to the (single) registered user's first conversation."""
    async with TestingSessionLocal() as db:
        user = (await db.execute(select(User))).scalars().first()
        assert user is not None
        conversation = Conversation(user_id=user.id, title="Seed")
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

        log = UsageLog(
            user_id=user.id,
            conversation_id=conversation.id,
            model="test-model",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        db.add(log)
        await db.commit()
        if created_at is not None:
            log.created_at = created_at
            await db.commit()


def _tokens_worth(usd: float) -> int:
    """Completion tokens whose configured price is roughly `usd`."""
    return int((usd / settings.LLM_OUTPUT_COST_PER_1M_TOKENS) * 1_000_000)


@pytest.mark.asyncio
async def test_month_to_date_cost_sums_only_the_current_month(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await _seed_usage(1_000_000, 1_000_000)
    # Dated into last month, so it must not count toward this month's spend.
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=5)
    await _seed_usage(5_000_000, 5_000_000, created_at=last_month)

    async with TestingSessionLocal() as db:
        user = (await db.execute(select(User))).scalars().first()
        assert user is not None
        spent = await month_to_date_cost_usd(db, user.id)

    expected = settings.LLM_INPUT_COST_PER_1M_TOKENS + settings.LLM_OUTPUT_COST_PER_1M_TOKENS
    assert spent == pytest.approx(expected)


@pytest.mark.asyncio
async def test_chat_is_blocked_once_the_monthly_cap_is_reached(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MONTHLY_COST_CAP_USD", 1.0)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key")

    resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = resp.json()["id"]

    await _seed_usage(0, _tokens_worth(1.5))

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        data={"content": "hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 429
    assert "monthly usage limit" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_usage_below_the_cap_is_allowed_through(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MONTHLY_COST_CAP_USD", 10.0)
    # No API key configured, so the route stops at its own 503 guard — which is
    # past the cap check, and therefore proof the cap did not reject the turn.
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")

    resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = resp.json()["id"]

    await _seed_usage(0, _tokens_worth(0.5))

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        data={"content": "hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_cap_of_zero_disables_the_check(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MONTHLY_COST_CAP_USD", 0.0)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")

    resp = await client.post("/api/v1/conversations", json={}, headers=auth_headers)
    conversation_id = resp.json()["id"]

    await _seed_usage(0, _tokens_worth(500.0))

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        data={"content": "hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 503
