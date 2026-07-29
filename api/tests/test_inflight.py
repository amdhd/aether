"""The per-user ceiling that makes the monthly cost cap enforceable.

Without it, simultaneous turns all read the same month-to-date spend — which is
only written once a turn *ends* — so they all pass the cap together.
"""

import pytest

from app.core.config import settings
from app.core.inflight import acquire_turn_slot, release_turn_slot, reset_inflight

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean_slots():
    reset_inflight()
    yield
    reset_inflight()


async def test_allows_up_to_the_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 2)

    assert await acquire_turn_slot(1) is True
    assert await acquire_turn_slot(1) is True


async def test_rejects_the_turn_past_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 2)

    await acquire_turn_slot(1)
    await acquire_turn_slot(1)
    # This is the request that would otherwise race the cost cap.
    assert await acquire_turn_slot(1) is False


async def test_releasing_frees_the_slot_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    assert await acquire_turn_slot(1) is True
    assert await acquire_turn_slot(1) is False

    await release_turn_slot(1)
    assert await acquire_turn_slot(1) is True


async def test_limit_is_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    assert await acquire_turn_slot(1) is True
    # One busy user must not block everybody else.
    assert await acquire_turn_slot(2) is True


async def test_extra_releases_cannot_bank_spare_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    await release_turn_slot(1)
    await release_turn_slot(1)

    assert await acquire_turn_slot(1) is True
    assert await acquire_turn_slot(1) is False


async def test_zero_disables_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 0)

    for _ in range(5):
        assert await acquire_turn_slot(1) is True
