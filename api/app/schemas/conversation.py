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
    attachment_name: str | None = None
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead] = Field(default_factory=list)
