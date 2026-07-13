import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.client import get_deepseek_client
from app.agent.memory import maybe_summarize_history
from app.agent.personas import get_system_prompt
from app.agent.tools import TOOL_SCHEMAS, call_tool
from app.core.config import settings
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.usage_log import UsageLog
from app.models.user import User

MAX_TOOL_ITERATIONS = 5


def _usage_log(user: User, conversation: Conversation, usage: dict[str, int]) -> UsageLog:
    return UsageLog(
        user_id=user.id,
        conversation_id=conversation.id,
        model=settings.DEEPSEEK_MODEL,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
    )


def _message_to_api(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = message.tool_calls
    if message.tool_call_id:
        out["tool_call_id"] = message.tool_call_id
    if message.role == MessageRole.tool and out["content"] is None:
        out["content"] = ""
    if message.role == MessageRole.assistant and message.reasoning_content:
        # deepseek-v4-flash runs in thinking mode by default and requires
        # reasoning_content to be echoed back for prior assistant turns,
        # otherwise it returns a 400.
        out["reasoning_content"] = message.reasoning_content
    return out


async def _build_context(db: AsyncSession, conversation: Conversation) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": get_system_prompt(conversation.persona)},
        {
            "role": "system",
            "content": f"Current date/time (UTC): {datetime.now(timezone.utc).isoformat()}",
        },
    ]
    if conversation.memory_summary:
        messages.append(
            {
                "role": "system",
                "content": f"Summary of earlier conversation:\n{conversation.memory_summary}",
            }
        )

    history_stmt = select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    if conversation.memory_summarized_until_id:
        history_stmt = history_stmt.where(Message.id > conversation.memory_summarized_until_id)
    history = await db.scalars(history_stmt)
    for message in history.all():
        messages.append(_message_to_api(message))
    return messages


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_agent_response(
    db: AsyncSession,
    user: User,
    conversation: Conversation,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """Persist the user's message, run the tool-calling agent loop against
    DeepSeek, and yield SSE-formatted events as the response streams in."""

    # The request's db session may already be torn down by the time this
    # generator runs (FastAPI closes `yield`-dependencies before streaming
    # the response body), which detaches `conversation`. Re-fetch it so
    # mutations (e.g. the title update below) are tracked and persisted.
    conversation = await db.get(Conversation, conversation.id)

    db.add(Message(conversation_id=conversation.id, role=MessageRole.user, content=user_message))
    await db.commit()

    client = get_deepseek_client()
    await maybe_summarize_history(db, conversation, client)
    messages = await _build_context(db, conversation)

    for _ in range(MAX_TOOL_ITERATIONS):
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_chunks: dict[int, dict[str, str]] = {}
        usage: dict[str, int] | None = None

        # The upstream call and the token stream can fail mid-flight (provider
        # 5xx, network drop). Because response headers are already sent by the
        # time this generator runs, an unhandled exception would just truncate
        # the SSE stream with no signal to the client, so surface it as an
        # explicit `error` event instead.
        try:
            stream = await client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                stream=True,
                stream_options={"include_usage": True},
                extra_body={"thinking": {"type": "enabled"}},
            )

            async for chunk in stream:
                if chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                    }
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                if getattr(delta, "reasoning_content", None):
                    reasoning_parts.append(delta.reasoning_content)
                    yield _sse_event("reasoning", {"content": delta.reasoning_content})

                if delta.content:
                    content_parts.append(delta.content)
                    yield _sse_event("token", {"content": delta.content})

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        entry = tool_call_chunks.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                entry["name"] += tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments
        except Exception:
            yield _sse_event(
                "error", {"message": "The assistant hit an error while responding. Please try again."}
            )
            return

        content = "".join(content_parts) or None
        reasoning = "".join(reasoning_parts) or None

        if tool_call_chunks:
            tool_calls = [
                {
                    "id": entry["id"],
                    "type": "function",
                    "function": {"name": entry["name"], "arguments": entry["arguments"]},
                }
                for _, entry in sorted(tool_call_chunks.items())
            ]

            assistant_msg = Message(
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content=content,
                reasoning_content=reasoning,
                tool_calls=tool_calls,
            )
            db.add(assistant_msg)
            if usage:
                db.add(_usage_log(user, conversation, usage))
            await db.commit()
            messages.append(_message_to_api(assistant_msg))

            for tool_call in tool_calls:
                yield _sse_event("tool_call", {"name": tool_call["function"]["name"]})
                try:
                    arguments = json.loads(tool_call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                tool_result = await call_tool(tool_call["function"]["name"], arguments, db, user)

                tool_msg = Message(
                    conversation_id=conversation.id,
                    role=MessageRole.tool,
                    content=tool_result,
                    tool_call_id=tool_call["id"],
                    tool_name=tool_call["function"]["name"],
                )
                db.add(tool_msg)
                messages.append(_message_to_api(tool_msg))

            await db.commit()
            continue

        assistant_msg = Message(
            conversation_id=conversation.id,
            role=MessageRole.assistant,
            content=content,
            reasoning_content=reasoning,
        )
        db.add(assistant_msg)

        if usage:
            db.add(_usage_log(user, conversation, usage))

        if conversation.title == "New conversation":
            conversation.title = user_message.strip()[:60] or "New conversation"

        await db.commit()
        yield _sse_event("done", {"conversation_title": conversation.title})
        return

    yield _sse_event("error", {"message": "The assistant could not complete the request."})
