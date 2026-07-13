from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.usage_log import UsageLog

# Keep this many of the most recent messages verbatim in context.
KEEP_RECENT_MESSAGES = 10

# Roughly 6000 tokens at ~4 chars/token, per the spec's summarization threshold.
SUMMARIZE_CHAR_THRESHOLD = 24000

SUMMARY_SYSTEM_PROMPT = (
    "You are a summarization assistant for an AI personal assistant app. "
    "Condense the conversation excerpt into a concise summary that preserves "
    "key facts, decisions, names, dates, preferences, and any unresolved "
    "questions, so the assistant can continue the conversation naturally "
    "without access to the original messages. Write the summary in plain "
    "prose, third person, a few short paragraphs at most."
)


def _message_char_len(message: Message) -> int:
    """Approximate the context-window footprint of a message. Counts not just
    the visible content but the reasoning trace and serialized tool-call
    arguments too, since in a tool-heavy chat those dominate the token count and
    a content-only estimate would under-trigger summarization."""
    size = len(message.content or "") + len(message.reasoning_content or "")
    if message.tool_calls:
        for call in message.tool_calls:
            function = call.get("function", {})
            size += len(function.get("name", "")) + len(function.get("arguments", ""))
    return size


def _format_message_for_summary(message: Message) -> str:
    if message.role == MessageRole.tool:
        return f"[result from tool '{message.tool_name}']: {message.content}"
    if message.tool_calls:
        calls = ", ".join(tc["function"]["name"] for tc in message.tool_calls)
        return f"assistant (used tools: {calls}): {message.content or ''}"
    return f"{message.role.value}: {message.content or ''}"


async def maybe_summarize_history(db: AsyncSession, conversation: Conversation, client: AsyncOpenAI) -> None:
    """Fold older messages into conversation.memory_summary once the
    unsummarized history grows past SUMMARIZE_CHAR_THRESHOLD, keeping the
    most recent KEEP_RECENT_MESSAGES verbatim."""

    stmt = select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    if conversation.memory_summarized_until_id:
        stmt = stmt.where(Message.id > conversation.memory_summarized_until_id)
    unsummarized = list(await db.scalars(stmt))

    if len(unsummarized) <= KEEP_RECENT_MESSAGES:
        return

    to_fold = unsummarized[:-KEEP_RECENT_MESSAGES]
    if sum(_message_char_len(m) for m in to_fold) < SUMMARIZE_CHAR_THRESHOLD:
        return

    transcript = "\n".join(_format_message_for_summary(m) for m in to_fold)
    user_content = (
        f"Existing summary:\n{conversation.memory_summary}\n\n" if conversation.memory_summary else ""
    ) + f"Conversation excerpt to fold into the summary:\n{transcript}"

    response = await client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        stream=False,
        extra_body={"thinking": {"type": "disabled"}},
    )
    summary = response.choices[0].message.content
    if not summary:
        return

    # The summarization call itself burns tokens; meter it so usage analytics
    # reflect true cost rather than only the user-facing completions.
    usage = getattr(response, "usage", None)
    if usage is not None:
        db.add(
            UsageLog(
                user_id=conversation.user_id,
                conversation_id=conversation.id,
                model=settings.DEEPSEEK_MODEL,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
            )
        )

    conversation.memory_summary = summary
    conversation.memory_summarized_until_id = to_fold[-1].id
    await db.commit()
