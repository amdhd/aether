import json
from types import SimpleNamespace

import pytest

from app.agent.budget import (
    INPUT_TOKEN_BUDGET, MAX_TOOL_RESULT_BYTES, ContextBudgetExceeded,
    bound_context, bounded_tool_result, input_token_upper_bound,
)
from app.agent.tools import call_tool
from app.core.security import decode_token
from app.models.note import Note
from app.models.user import User
from tests.conftest import TestingSessionLocal


def test_context_budget_counts_unicode_reasoning_and_tool_schemas():
    messages = [{"role": "system", "content": "policy"}]
    for _ in range(20):
        messages.extend([{"role": "user", "content": "界" * 8000},
                         {"role": "assistant", "content": "answer", "reasoning_content": "x" * 20000}])
    tools = [{"schema": "x" * 5000}]
    bounded = bound_context(messages, tools)
    assert input_token_upper_bound(bounded, tools) <= INPUT_TOKEN_BUDGET
    assert len(bounded) < len(messages)
    assert messages[1]["content"] == "界" * 8000


def test_context_keeps_tool_calls_and_results_together():
    call = {"id": "call1", "type": "function", "function": {"name": "list_tasks", "arguments": "{}"}}
    messages = [{"role": "user", "content": "old" * 10000}] * 15 + [
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "call1", "content": "result"},
    ]
    bounded = bound_context(messages)
    assert bounded[-2]["tool_calls"] == [call]
    assert bounded[-1]["tool_call_id"] == "call1"


def test_oversized_structural_input_fails_before_provider_call():
    with pytest.raises(ContextBudgetExceeded):
        bound_context([{"role": "user", "content": "hello"}], [{"schema": "x" * INPUT_TOKEN_BUDGET}])


def test_tool_size_limit_preserves_valid_json_and_pagination_cursor():
    result = {"notes": [{"id": i, "content": "界" * 2000} for i in range(50, 0, -1)], "next_before_id": None}
    encoded = bounded_tool_result(result)
    assert len(encoded.encode()) <= MAX_TOOL_RESULT_BYTES
    page = json.loads(encoded)
    assert page["truncated"] is True
    assert page["next_before_id"] == page["notes"][-1]["id"]


async def test_tool_pages_are_bounded_and_owner_scoped(client, auth_headers):
    user_id = int(decode_token(auth_headers["Authorization"].split()[1])["sub"])
    async with TestingSessionLocal() as db:
        other = User(email="private@example.com", name="Other", password_hash="unused")
        db.add(other)
        await db.flush()
        db.add_all([Note(user_id=user_id, title=f"Note {i}", content="small") for i in range(55)])
        db.add(Note(user_id=other.id, title="PRIVATE", content="secret"))
        await db.commit()
        user = SimpleNamespace(id=user_id)
        first = json.loads(await call_tool("list_notes", {"limit": 50}, db, user))
        second = json.loads(await call_tool("list_notes", {"limit": 50, "before_id": first["next_before_id"]}, db, user))
        assert len(first["notes"]) == 50
        assert len(second["notes"]) == 5
        assert second["next_before_id"] is None
        assert not ({row["id"] for row in first["notes"]} & {row["id"] for row in second["notes"]})
        assert all(row["title"] != "PRIVATE" for row in first["notes"] + second["notes"])
        invalid = json.loads(await call_tool("list_notes", {"limit": 1000000}, db, user))
        assert "error" in invalid
