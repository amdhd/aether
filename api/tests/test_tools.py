import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent.tools import _weather_cache, call_tool
from app.core.config import settings
from app.core.rate_limit import reset_rate_limits


class _FakeResponse:
    def __init__(self, json_data: object, status_code: int = 200) -> None:
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> object:
        return self._json


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> _FakeResponse:
        return self._response

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None) -> _FakeResponse:
        return self._response

    async def delete(self, url: str, headers: dict | None = None) -> _FakeResponse:
        return self._response


WEATHER_SAMPLE = [
    {
        "location": {"location_id": "Tn003", "location_name": "Alor Star"},
        "date": "2026-06-11",
        "morning_forecast": "Tiada hujan",
        "afternoon_forecast": "Hujan",
        "night_forecast": "Tiada hujan",
        "summary_forecast": "Hujan di petang",
        "summary_when": "Petang",
        "min_temp": 24,
        "max_temp": 33,
    },
]


@pytest.fixture(autouse=True)
def _reset_weather_cache() -> None:
    _weather_cache["data"] = None
    _weather_cache["fetched_at"] = 0.0
    reset_rate_limits()


async def test_get_weather_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse(WEATHER_SAMPLE)),
    )

    result = json.loads(await call_tool("get_weather", {"location": "Alor Star"}, None, SimpleNamespace(id=1)))
    assert result["location"] == "Alor Star"
    assert result["max_temp_c"] == 33
    assert result["language"] == "ms"


async def test_get_weather_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse(WEATHER_SAMPLE)),
    )

    result = json.loads(await call_tool("get_weather", {"location": "Nonexistent City"}, None, SimpleNamespace(id=1)))
    assert "error" in result
    assert "did_you_mean" in result


async def test_web_search_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")

    result = json.loads(await call_tool("web_search", {"query": "test"}, None, SimpleNamespace(id=1)))
    assert "error" in result


async def test_web_search_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(
            _FakeResponse({"results": [{"title": "Result", "url": "https://example.com", "content": "Snippet"}]})
        ),
    )

    result = json.loads(await call_tool("web_search", {"query": "test"}, None, SimpleNamespace(id=1)))
    assert result["query"] == "test"
    assert result["results"] == [{"title": "Result", "url": "https://example.com", "content": "Snippet"}]


async def test_web_search_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(settings, "WEB_SEARCH_RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(
            _FakeResponse({"results": [{"title": "Result", "url": "https://example.com", "content": "Snippet"}]})
        ),
    )

    user = SimpleNamespace(id=1)
    for _ in range(2):
        result = json.loads(await call_tool("web_search", {"query": "test"}, None, user))
        assert "results" in result

    result = json.loads(await call_tool("web_search", {"query": "test"}, None, user))
    assert "error" in result
    assert "rate limit" in result["error"].lower()


async def test_calendar_list_events_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_token(db, user):
        return None

    monkeypatch.setattr("app.agent.tools.google_oauth.get_valid_access_token", fake_get_token)

    result = json.loads(await call_tool("calendar_list_events", {}, None, SimpleNamespace(id=1)))
    assert "error" in result
    assert "connect" in result["error"].lower()


async def test_calendar_list_events_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_token(db, user):
        return "access-token"

    monkeypatch.setattr("app.agent.tools.google_oauth.get_valid_access_token", fake_get_token)
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(
            _FakeResponse(
                {
                    "items": [
                        {
                            "id": "evt1",
                            "summary": "Team sync",
                            "start": {"dateTime": "2026-06-15T09:00:00+08:00"},
                            "end": {"dateTime": "2026-06-15T10:00:00+08:00"},
                        }
                    ]
                }
            )
        ),
    )

    result = json.loads(await call_tool("calendar_list_events", {}, None, SimpleNamespace(id=1)))
    assert result["events"][0]["id"] == "evt1"
    assert result["events"][0]["summary"] == "Team sync"


async def test_calendar_create_event_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_token(db, user):
        return "access-token"

    monkeypatch.setattr("app.agent.tools.google_oauth.get_valid_access_token", fake_get_token)
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(
            _FakeResponse(
                {
                    "id": "evt2",
                    "summary": "Doctor appointment",
                    "start": {"dateTime": "2026-06-16T09:00:00+08:00"},
                    "end": {"dateTime": "2026-06-16T10:00:00+08:00"},
                    "htmlLink": "https://calendar.google.com/event?eid=evt2",
                }
            )
        ),
    )

    result = json.loads(
        await call_tool(
            "calendar_create_event",
            {
                "summary": "Doctor appointment",
                "start": "2026-06-16T09:00:00+08:00",
                "end": "2026-06-16T10:00:00+08:00",
            },
            None,
            SimpleNamespace(id=1),
        )
    )
    assert result["event"]["id"] == "evt2"
    assert result["event"]["html_link"] == "https://calendar.google.com/event?eid=evt2"


async def test_calendar_delete_event_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_token(db, user):
        return "access-token"

    monkeypatch.setattr("app.agent.tools.google_oauth.get_valid_access_token", fake_get_token)
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse({}, status_code=204)),
    )

    result = json.loads(
        await call_tool("calendar_delete_event", {"event_id": "evt2"}, None, SimpleNamespace(id=1))
    )
    assert result["deleted"] == "evt2"


async def test_calendar_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_token(db, user):
        return "access-token"

    monkeypatch.setattr("app.agent.tools.google_oauth.get_valid_access_token", fake_get_token)
    monkeypatch.setattr(settings, "CALENDAR_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(
        "app.agent.tools.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse({"items": []})),
    )

    user = SimpleNamespace(id=1)
    result = json.loads(await call_tool("calendar_list_events", {}, None, user))
    assert "events" in result

    result = json.loads(await call_tool("calendar_list_events", {}, None, user))
    assert "error" in result
    assert "rate limit" in result["error"].lower()
