import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agent.tools import APPROVAL_REQUIRED_TOOLS, call_tool
from app.core.security import decode_token
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.task import Task
from tests.conftest import TestingSessionLocal


@pytest.mark.parametrize("name", sorted(APPROVAL_REQUIRED_TOOLS))
async def test_model_cannot_approve_its_own_action(name):
    result = json.loads(await call_tool(name, {"approved": True}, None, SimpleNamespace(id=1)))
    assert result["approval_required"] is True


async def proposal(client, auth_headers, *, expired=False):
    user_id = int(decode_token(auth_headers["Authorization"].split()[1])["sub"])
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
