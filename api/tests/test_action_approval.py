import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agent import tools
from app.agent.tools import APPROVAL_REQUIRED_TOOLS, call_tool
from app.core.security import decode_token
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.task import Task
from tests.conftest import TestingSessionLocal


@pytest.mark.parametrize("name", sorted(APPROVAL_REQUIRED_TOOLS))
async def test_model_cannot_approve_its_own_action(name, monkeypatch):
    executed = []

    async def handler(db, user, args):
        executed.append(args)
        return {"executed": True}

    monkeypatch.setitem(tools._TOOL_HANDLERS, name, handler)
    result = json.loads(await call_tool(name, {"approved": True}, None, SimpleNamespace(id=1)))
    # Either proposed or refused for bad arguments — never executed.
    assert result.get("approval_required") is True or "error" in result
    assert executed == []


async def proposal(client, auth_headers, *, expired=False):
    user_id = int(decode_token(auth_headers["Authorization"].split()[1], "access")["sub"])
    async with TestingSessionLocal() as db:
        task = Task(user_id=user_id, title="Keep unless explicitly approved")
        conversation = Conversation(user_id=user_id, title="Approval test")
        db.add_all([task, conversation])
        await db.flush()
        content = json.loads(await call_tool("delete_task", {"task_id": task.id}, db, SimpleNamespace(id=user_id)))
        if expired:
            content["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        receipt = Message(conversation_id=conversation.id, role=MessageRole.tool,
                          tool_name="delete_task", content=json.dumps(content), action_status="pending")
        db.add(receipt)
        await db.commit()
        return conversation.id, receipt.id, task.id


async def test_owner_approval_executes_once_and_cannot_replace_arguments(client, auth_headers):
    conversation_id, receipt_id, task_id = await proposal(client, auth_headers)
    url = f"/api/v1/conversations/{conversation_id}/actions/{receipt_id}"
    altered = await client.post(url, headers=auth_headers, json={"decision": "approve", "arguments": {"task_id": 999}})
    assert altered.status_code == 422
    response = await client.post(url, headers=auth_headers, json={"decision": "approve"})
    assert response.status_code == 200
    assert response.json()["action_status"] == "completed"
    assert (await client.post(url, headers=auth_headers, json={"decision": "approve"})).status_code == 409
    async with TestingSessionLocal() as db:
        assert await db.get(Task, task_id) is None


@pytest.mark.parametrize("decision,expired,expected", [("reject", False, "rejected"), ("approve", True, "expired")])
async def test_rejected_or_expired_actions_do_not_execute(client, auth_headers, decision, expired, expected):
    conversation_id, receipt_id, task_id = await proposal(client, auth_headers, expired=expired)
    response = await client.post(f"/api/v1/conversations/{conversation_id}/actions/{receipt_id}",
                                 headers=auth_headers, json={"decision": decision})
    assert response.status_code == 200
    assert response.json()["action_status"] == expected
    async with TestingSessionLocal() as db:
        assert await db.get(Task, task_id) is not None


async def test_another_user_cannot_approve_action(client, auth_headers):
    conversation_id, receipt_id, task_id = await proposal(client, auth_headers)
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "name": "Other", "password": "supersecret123"})
    login = await client.post("/api/v1/auth/login", data={"username": "other@example.com", "password": "supersecret123"})
    response = await client.post(f"/api/v1/conversations/{conversation_id}/actions/{receipt_id}",
                                 headers={"Authorization": f"Bearer {login.json()['access_token']}"},
                                 json={"decision": "approve"})
    assert response.status_code == 404
    async with TestingSessionLocal() as db:
        assert await db.get(Task, task_id) is not None
        assert (await db.scalar(select(Message).where(Message.id == receipt_id))).action_status == "pending"


# --- update_task: overwriting user-written text needs approval ---------------


async def _task_for(auth_headers, **fields):
    user_id = int(decode_token(auth_headers["Authorization"].split()[1], "access")["sub"])
    async with TestingSessionLocal() as db:
        task = Task(user_id=user_id, **fields)
        conversation = Conversation(user_id=user_id, title="Update approval")
        db.add_all([task, conversation])
        await db.commit()
        return user_id, conversation.id, task.id


@pytest.mark.parametrize("arguments", [
    {"title": "Hijacked", "approved": True},
    {"description": ""},
    {"description": "Replaced"},
])
async def test_update_task_overwriting_text_is_only_proposed(client, auth_headers, arguments):
    user_id, _, task_id = await _task_for(auth_headers, title="Original", description="Written by the user")
    async with TestingSessionLocal() as db:
        result = json.loads(await call_tool("update_task", {"task_id": task_id, **arguments},
                                            db, SimpleNamespace(id=user_id)))
    assert result["approval_required"] is True
    async with TestingSessionLocal() as db:
        task = await db.get(Task, task_id)
        assert (task.title, task.description) == ("Original", "Written by the user")


@pytest.mark.parametrize("arguments,field,value", [
    ({"status": "done"}, "status", "done"),
    ({"priority": "high"}, "priority", "high"),
    ({"title": "Original"}, "title", "Original"),  # unchanged text loses nothing
    ({"description": "Added later"}, "description", "Added later"),  # was empty
])
async def test_update_task_reversible_changes_apply_immediately(client, auth_headers, arguments, field, value):
    user_id, _, task_id = await _task_for(auth_headers, title="Original")
    async with TestingSessionLocal() as db:
        result = json.loads(await call_tool("update_task", {"task_id": task_id, **arguments},
                                            db, SimpleNamespace(id=user_id)))
    assert "approval_required" not in result
    stored = result["task"][field]
    assert stored == value


async def test_update_task_invalid_proposal_is_refused_not_proposed(client, auth_headers):
    user_id, _, task_id = await _task_for(auth_headers, title="Original")
    async with TestingSessionLocal() as db:
        result = json.loads(await call_tool("update_task", {"task_id": task_id, "title": "New", "due_date": 7},
                                            db, SimpleNamespace(id=user_id)))
    assert "error" in result and "approval_required" not in result


async def test_update_task_on_another_users_task_is_not_proposed(client, auth_headers):
    _, _, task_id = await _task_for(auth_headers, title="Original")
    async with TestingSessionLocal() as db:
        result = json.loads(await call_tool("update_task", {"task_id": task_id, "title": "Mine now"},
                                            db, SimpleNamespace(id=10_000)))
    assert "not found" in result["error"]


async def test_approved_update_task_applies_the_stored_arguments(client, auth_headers):
    user_id, conversation_id, task_id = await _task_for(auth_headers, title="Original")
    async with TestingSessionLocal() as db:
        content = await call_tool("update_task", {"task_id": task_id, "title": "Renamed"},
                                  db, SimpleNamespace(id=user_id))
        receipt = Message(conversation_id=conversation_id, role=MessageRole.tool,
                          tool_name="update_task", content=content, action_status="pending")
        db.add(receipt)
        await db.commit()
    response = await client.post(f"/api/v1/conversations/{conversation_id}/actions/{receipt.id}",
                                 headers=auth_headers, json={"decision": "approve"})
    assert response.status_code == 200
    assert response.json()["action_status"] == "completed"
    async with TestingSessionLocal() as db:
        assert (await db.get(Task, task_id)).title == "Renamed"


# --- proposals show the server-resolved target, re-checked on approval -------


async def test_delete_proposal_shows_the_real_task_not_the_models_description(client, auth_headers):
    conversation_id, receipt_id, task_id = await proposal(client, auth_headers)
    async with TestingSessionLocal() as db:
        content = json.loads((await db.get(Message, receipt_id)).content)
    assert content["target"]["kind"] == "task"
    assert content["target"]["title"] == "Keep unless explicitly approved"


async def test_delete_proposal_for_another_users_task_is_refused(client, auth_headers):
    _, _, task_id = await proposal(client, auth_headers)
    async with TestingSessionLocal() as db:
        result = json.loads(await call_tool("delete_task", {"task_id": task_id}, db, SimpleNamespace(id=10_000)))
    assert "approval_required" not in result
    assert "not found" in result["error"]


async def test_approval_does_not_execute_if_the_task_changed_after_proposal(client, auth_headers):
    conversation_id, receipt_id, task_id = await proposal(client, auth_headers)
    async with TestingSessionLocal() as db:
        (await db.get(Task, task_id)).title = "Edited after the user saw the card"
        await db.commit()
    response = await client.post(f"/api/v1/conversations/{conversation_id}/actions/{receipt_id}",
                                 headers=auth_headers, json={"decision": "approve"})
    assert response.status_code == 200
    assert response.json()["action_status"] == "failed"
    assert "changed after" in json.loads(response.json()["content"])["error"]
    async with TestingSessionLocal() as db:
        assert await db.get(Task, task_id) is not None


async def test_receipt_without_a_resolved_target_is_not_executed(client, auth_headers):
    conversation_id, receipt_id, task_id = await proposal(client, auth_headers)
    async with TestingSessionLocal() as db:
        receipt = await db.get(Message, receipt_id)
        content = json.loads(receipt.content)
        content.pop("target")
        receipt.content = json.dumps(content)
        await db.commit()
    response = await client.post(f"/api/v1/conversations/{conversation_id}/actions/{receipt_id}",
                                 headers=auth_headers, json={"decision": "approve"})
    assert response.json()["action_status"] == "failed"
    async with TestingSessionLocal() as db:
        assert await db.get(Task, task_id) is not None


class _CalendarLookup:
    def __init__(self, status_code, body=None):
        self.status_code, self.body = status_code, body or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None):
        import httpx

        request = httpx.Request("GET", url)
        return httpx.Response(self.status_code, json=self.body, request=request)


@pytest.fixture
def calendar_connected(monkeypatch):
    async def token(db, user):
        return "access-token"

    monkeypatch.setattr("app.agent.tools.google_oauth.get_valid_access_token", token)


async def test_calendar_delete_proposal_shows_the_event_google_holds(calendar_connected, monkeypatch):
    event = {"id": "evt1", "summary": "Board meeting", "status": "confirmed",
             "start": {"dateTime": "2026-12-01T09:00:00+08:00"}, "end": {"dateTime": "2026-12-01T10:00:00+08:00"}}
    monkeypatch.setattr("app.agent.tools.httpx.AsyncClient", lambda **kw: _CalendarLookup(200, event))
    result = json.loads(await call_tool("calendar_delete_event", {"event_id": "evt1"}, None, SimpleNamespace(id=1)))
    assert result["approval_required"] is True
    assert result["target"] == {"kind": "calendar_event", "summary": "Board meeting",
                                "start": "2026-12-01T09:00:00+08:00", "end": "2026-12-01T10:00:00+08:00",
                                "location": None}


@pytest.mark.parametrize("status_code", [404, 410])
async def test_calendar_delete_proposal_for_missing_event_is_refused(calendar_connected, monkeypatch, status_code):
    monkeypatch.setattr("app.agent.tools.httpx.AsyncClient", lambda **kw: _CalendarLookup(status_code))
    result = json.loads(await call_tool("calendar_delete_event", {"event_id": "nope"}, None, SimpleNamespace(id=1)))
    assert "approval_required" not in result
    assert "not on your calendar" in result["error"]
