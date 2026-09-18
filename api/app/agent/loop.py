import json
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.client import get_deepseek_client
from app.agent.memory import maybe_summarize_history
from app.agent.personas import get_system_prompt
from app.agent.tools import TOOL_SCHEMAS, UNTRUSTED_RESULT_TOOLS, call_tool, format_tool_result_block
from app.core import metrics
from app.core.config import settings
from app.core.inflight import release_turn_slot
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.agent.redaction import VendorRedactor
from app.models.usage_log import UsageLog
from app.models.user import User
from app.services.attachments import (
    MAX_ATTACHMENT_BYTES,
    format_attachment_block,
    sanitize_attachment_name,
)

logger = get_logger(__name__)

MAX_TOOL_ITERATIONS = 5

# What the client is told when a turn fails after headers are sent. Shared so the
# several places that can reach it cannot drift apart.
TURN_FAILED_MESSAGE = "The assistant hit an error while responding. Please try again."

# Ceiling on attachment text held verbatim in one context, summed across every
# message in it. The upload path bounds each *file* at MAX_ATTACHMENT_BYTES, but
# nothing bounded the sum: the recent-message window is a count of messages, so
# ten recent messages each carrying a 200 KB CSV built a ~2 MB context — far past
# any model's window — and the turn died on a provider 400.
#
# Pinned to the per-file ceiling, so the worst case a whole conversation can cost
# is what the app already accepts for a single upload.
MAX_ATTACHMENT_CONTEXT_CHARS = MAX_ATTACHMENT_BYTES


def _usage_log(user: User, conversation: Conversation, usage: dict[str, int]) -> UsageLog:
    return UsageLog(
        user_id=user.id,
        conversation_id=conversation.id,
        model=settings.DEEPSEEK_MODEL,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
    )


def _message_to_api(message: Message, *, include_attachment: bool = True) -> dict[str, Any]:
    content = message.content
    if message.role == MessageRole.user and message.attachment_content:
        if include_attachment:
            block = format_attachment_block(message.attachment_name or "", message.attachment_content)
        else:
            # The file is over the context budget, but silently dropping it would
            # leave the model answering about a file it cannot see and unable to
            # say so. Keep the fact, lose the payload.
            block = (
                f"[The user uploaded a file named "
                f"{sanitize_attachment_name(message.attachment_name or '')!r} earlier in this "
                "conversation. Its contents are no longer in context, having been displaced by "
                "more recent uploads. Ask the user to re-send it if you need it.]"
            )
        content = f"{content or ''}\n\n{block}".strip()
    if message.role == MessageRole.tool and message.tool_name in UNTRUSTED_RESULT_TOOLS:
        # Web pages and calendar invites are written by third parties, so their
        # text is fenced as data before it reaches the model. See tools.py.
        content = format_tool_result_block(message.tool_name, content or "")
    out: dict[str, Any] = {"role": message.role.value, "content": content}
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


def _repair_orphaned_tool_calls(history: list[Message]) -> list[Message]:
    """Stand in a result for any tool call whose own row never got written.

    The provider rejects an assistant message carrying `tool_calls` unless every
    one of those ids is answered by a following `tool` message. History is
    replayed verbatim on every turn, so a single gap does not fail one turn — it
    fails *every* turn in that conversation from then on, permanently, and the
    user's only escape is deleting the conversation.

    A gap is written whenever a turn dies between persisting the assistant's tool
    call and persisting its result: an unhandled tool exception, a deploy, a task
    kill. The write path is ordered to make that window as small as it can be
    (see the tool loop below), but it cannot be closed entirely while tool
    handlers commit the session they share with this loop. So the read path
    treats a gap as something to survive rather than something that cannot
    happen, which also unbricks conversations already damaged by an earlier
    version.
    """
    answered = {m.tool_call_id for m in history if m.role == MessageRole.tool and m.tool_call_id}
    repaired: list[Message] = []
    for message in history:
        repaired.append(message)
        if message.role != MessageRole.assistant or not message.tool_calls:
            continue
        for call in message.tool_calls:
            call_id = call.get("id")
            if not call_id or call_id in answered:
                continue
            logger.warning(
                "context.orphaned_tool_call conversation_id=%s tool_call_id=%s",
                message.conversation_id,
                call_id,
            )
            # Transient: constructed for this context only, never added to the
            # session. Writing it back would be repairing history we cannot
            # reconstruct. tool_name is left unset because this text is ours, so
            # it must not be fenced as third-party output.
            repaired.append(
                Message(
                    conversation_id=message.conversation_id,
                    role=MessageRole.tool,
                    content=json.dumps({"error": "This tool call did not complete."}),
                    tool_call_id=call_id,
                )
            )
    return repaired


def _attachments_within_budget(history: list[Message]) -> set[int]:
    """Ids of the messages whose attachment text still fits in one context.

    Walked newest first, stopping at the first file that does not fit rather than
    skipping it to squeeze in an older one: a context holding an old upload while
    the newest is missing is a worse thing to hand a model than a shorter run of
    the most recent ones.

    Summarization is what normally bounds a long history, but it cannot help
    here. It only folds messages *older* than the recent window, and that window
    is a count of messages — so a run of large uploads inside it is out of its
    reach no matter how big it gets.
    """
    kept: set[int] = set()
    remaining = MAX_ATTACHMENT_CONTEXT_CHARS
    for message in reversed(history):
        if not message.attachment_content:
            continue
        if len(message.attachment_content) > remaining:
            break
        remaining -= len(message.attachment_content)
        kept.add(message.id)
    return kept


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
    history = list(await db.scalars(history_stmt))
    budgeted = _attachments_within_budget(history)
    for message in _repair_orphaned_tool_calls(history):
        messages.append(
            _message_to_api(message, include_attachment=message.id in budgeted)
        )
    return messages


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_agent_response(
    session_factory: async_sessionmaker[AsyncSession],
    user: User,
    conversation_id: int,
    user_message: str,
    attachment_name: str | None = None,
    attachment_content: str | None = None,
) -> AsyncGenerator[str, None]:
    """Persist the user's message, run the tool-calling agent loop against
    DeepSeek, and yield SSE-formatted events as the response streams in.

    An optional parsed file attachment (e.g. a campaign CSV) is stored on the
    user message and injected into the model context by ``_message_to_api``, so
    it remains available for follow-up turns without cluttering the chat bubble.

    This generator outlives the request's DB dependency: FastAPI tears down
    `yield`-dependencies before the streaming body runs. So instead of borrowing
    the (already-closed) request session, it opens and *owns* a session for the
    duration of the stream. The `async with` closes it when the stream finishes
    normally or when the client disconnects (Starlette calls `aclose()`), which
    releases the connection and prevents it lingering idle-in-transaction and
    holding locks — a leak that is invisible on SQLite but deadlocks Postgres.

    The route claimed this user's in-flight turn slot before handing the
    generator to Starlette, so releasing it here covers every way the turn can
    end: normal completion, an error, or the client disconnecting mid-stream."""
    try:
        async with session_factory() as db:
            async for event in _run_agent(
                db, user, conversation_id, user_message, attachment_name, attachment_content
            ):
                yield event
    finally:
        await release_turn_slot(user.id)


async def _run_agent(
    db: AsyncSession,
    user: User,
    conversation_id: int,
    user_message: str,
    attachment_name: str | None = None,
    attachment_content: str | None = None,
) -> AsyncGenerator[str, None]:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        # The route verified ownership in a separate (now-closed) session; this
        # generator re-fetches in its own session, which runs later. A conversation
        # deleted in that window (e.g. from another tab) is gone by the time we
        # look — surface a clean SSE error instead of raising and truncating.
        yield _sse_event("error", {"message": "This conversation no longer exists."})
        return

    db.add(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.user,
            content=user_message,
            attachment_name=attachment_name,
            attachment_content=attachment_content,
        )
    )
    await db.commit()

    client = get_deepseek_client()

    # Everything from here on runs with the response headers already sent, so an
    # exception that escapes this generator cannot become an HTTP error — it just
    # truncates the SSE stream, and the user watches a bubble that never resolves.
    # The streaming call below has had a handler for that since it was written;
    # these two did not, though both can fail the same way.

    # Summarizing is a provider call, so it fails whenever the provider does. It
    # is also best-effort: the summary is a cache of older history, and skipping
    # it costs a longer context, not a wrong answer. So log and carry on.
    try:
        await maybe_summarize_history(db, conversation, client)
    except Exception:
        logger.exception(
            "llm.summarize.failed user_id=%s conversation_id=%s", user.id, conversation.id
        )

    # Building the context is not best-effort — there is no turn without it — so
    # this one surfaces as an `error` event instead. It also catches a session
    # left unusable by a failed summarize commit above, which is why it follows
    # rather than shares that handler.
    try:
        messages = await _build_context(db, conversation)
    except Exception:
        logger.exception(
            "llm.context.failed user_id=%s conversation_id=%s", user.id, conversation.id
        )
        yield _sse_event("error", {"message": TURN_FAILED_MESSAGE})
        return

    for _ in range(MAX_TOOL_ITERATIONS):
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_chunks: dict[int, dict[str, str]] = {}
        usage: dict[str, int] | None = None
        # Redact vendor names from both streams. Applied to what is *appended* to
        # the parts lists too, so the stored message matches what the user saw.
        content_redactor = VendorRedactor()
        reasoning_redactor = VendorRedactor()

        # The upstream call and the token stream can fail mid-flight (provider
        # 5xx, network drop). Because response headers are already sent by the
        # time this generator runs, an unhandled exception would just truncate
        # the SSE stream with no signal to the client, so surface it as an
        # explicit `error` event instead.
        started_at = time.monotonic()
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
                    safe = reasoning_redactor.feed(delta.reasoning_content)
                    if safe:
                        reasoning_parts.append(safe)
                        yield _sse_event("reasoning", {"content": safe})

                if delta.content:
                    safe = content_redactor.feed(delta.content)
                    if safe:
                        content_parts.append(safe)
                        yield _sse_event("token", {"content": safe})

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

            # Release each redactor's held-back tail now the stream has ended.
            for redactor, parts, event in (
                (reasoning_redactor, reasoning_parts, "reasoning"),
                (content_redactor, content_parts, "token"),
            ):
                tail = redactor.flush()
                if tail:
                    parts.append(tail)
                    yield _sse_event(event, {"content": tail})
        except Exception:
            logger.exception(
                "llm.turn.failed user_id=%s conversation_id=%s latency_ms=%d",
                user.id,
                conversation.id,
                int((time.monotonic() - started_at) * 1000),
            )
            yield _sse_event("error", {"message": TURN_FAILED_MESSAGE})
            return

        prompt_tokens = usage["prompt_tokens"] if usage else 0
        completion_tokens = usage["completion_tokens"] if usage else 0
        latency_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "llm.turn user_id=%s conversation_id=%s prompt_tokens=%s completion_tokens=%s "
            "tool_calls=%d latency_ms=%d est_cost_usd=%s",
            user.id,
            conversation.id,
            usage["prompt_tokens"] if usage else None,
            usage["completion_tokens"] if usage else None,
            len(tool_call_chunks),
            latency_ms,
            metrics.estimate_cost_usd(prompt_tokens, completion_tokens),
        )
        # Emit the same facts as a CloudWatch EMF metric line (no-op unless
        # EMF metrics are enabled) so token/cost/latency become alarmable metrics.
        metrics.emit_llm_turn(
            model=settings.DEEPSEEK_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tool_calls=len(tool_call_chunks),
            latency_ms=latency_ms,
        )

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
            # Deliberately *not* committed here. An assistant message carrying
            # tool_calls is only replayable alongside the `tool` rows answering
            # it (see _repair_orphaned_tool_calls), so the two are held pending
            # and committed together once the loop below has built them.
            #
            # That makes the pair atomic for read-only tools, which is the common
            # case. It cannot for a tool that writes: _create_task and friends
            # commit the session this loop shares with them, and that commit
            # flushes whatever is pending, including this message. Closing the
            # window completely means giving tool handlers their own session —
            # worth doing, but a wider change than this. The read path covers
            # what remains.
            await db.flush()
            messages.append(_message_to_api(assistant_msg))

            for tool_call in tool_calls:
                tool_name = tool_call["function"]["name"]
                yield _sse_event("tool_call", {"name": tool_name})
                try:
                    arguments = json.loads(tool_call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                logger.info("tool.call user_id=%s tool=%s", user.id, tool_name)
                tool_result = await call_tool(tool_name, arguments, db, user)

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
