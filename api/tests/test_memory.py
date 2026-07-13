from types import SimpleNamespace

from sqlalchemy import func, select

from app.agent import memory
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.usage_log import UsageLog
from app.models.user import User
from tests.conftest import TestingSessionLocal


class _FakeCompletions:
    def __init__(self, response: SimpleNamespace) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _summary_response(text: str, usage: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=usage,
    )


def _fake_client(response: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(response)))


async def _seed_conversation(reasoning_len: int) -> int:
    """Create a conversation whose oldest two messages carry large *reasoning*
    payloads but near-empty content, plus 10 recent messages to keep."""
    async with TestingSessionLocal() as db:
        user = User(email="mem@example.com", name="Mem", password_hash="x")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        conversation = Conversation(user_id=user.id, title="t")
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

        # 2 old messages to fold + 10 to keep (> KEEP_RECENT_MESSAGES).
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content="",
                reasoning_content="r" * reasoning_len,
            )
        )
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content="",
                reasoning_content="r" * reasoning_len,
            )
        )
        for _ in range(10):
            db.add(Message(conversation_id=conversation.id, role=MessageRole.user, content="hi"))
        await db.commit()
        return conversation.id


async def test_summarizes_on_reasoning_payload_and_meters_usage() -> None:
    # Content is empty; only reasoning traces push the folded messages over the
    # 24000-char threshold. A content-only size estimate would never trigger.
    conversation_id = await _seed_conversation(reasoning_len=13000)
    client = _fake_client(
        _summary_response("A summary.", usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40))
    )

    async with TestingSessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        await memory.maybe_summarize_history(db, conversation, client)

    async with TestingSessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation.memory_summary == "A summary."
        assert conversation.memory_summarized_until_id is not None

        # The summarization call's tokens were recorded to usage analytics.
        totals = (
            await db.execute(
                select(func.coalesce(func.sum(UsageLog.prompt_tokens), 0), func.count()).where(
                    UsageLog.conversation_id == conversation_id
                )
            )
        ).one()
        assert totals == (100, 1)


async def test_no_summary_when_below_threshold() -> None:
    # Small reasoning payloads stay under the threshold -> no LLM call, no usage.
    conversation_id = await _seed_conversation(reasoning_len=100)
    client = _fake_client(_summary_response("unused"))

    async with TestingSessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        await memory.maybe_summarize_history(db, conversation, client)

    assert client.chat.completions.calls == []
    async with TestingSessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation.memory_summary is None
        usage_count = await db.scalar(
            select(func.count()).select_from(UsageLog).where(UsageLog.conversation_id == conversation_id)
        )
        assert usage_count == 0
