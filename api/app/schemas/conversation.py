from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.conversation import Persona
from app.models.message import MessageRole


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=255)
    persona: Persona = Persona.productivity_coach


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    persona: Persona | None = None


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    persona: Persona
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: MessageRole
    content: str | None
    reasoning_content: str | None
    tool_calls: list[dict[str, Any]] | None
    tool_name: str | None
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead] = Field(default_factory=list)


# Upper bound on a single chat turn. Generous enough to paste a long note or
# article (~4k tokens), but bounded so one request can't ship an unbounded
# payload — memory + token-cost abuse the per-minute rate limit alone doesn't
# stop. Kept under the summarization threshold (memory.SUMMARIZE_CHAR_THRESHOLD)
# so a lone message can't blow the context budget on its own. Tunable.
MAX_MESSAGE_CHARS = 16000


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
