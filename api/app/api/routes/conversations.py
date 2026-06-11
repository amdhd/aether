from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import stream_agent_response
from app.api.deps import get_current_user, get_owned_or_404
from app.core.config import settings
from app.core.rate_limit import enforce_chat_rate_limit
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.user import User
from app.schemas.common import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.conversation import (
    ChatMessageCreate,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    ConversationUpdate,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _get_owned_conversation(conversation_id: int, user: User, db: AsyncSession) -> Conversation:
    return await get_owned_or_404(db, Conversation, conversation_id, user, "Conversation not found")


@router.get("", response_model=Page[ConversationRead])
async def list_conversations(
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[ConversationRead]:
    base_stmt = select(Conversation).where(Conversation.user_id == current_user.id)
    total = await db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
    stmt = base_stmt.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)
    result = await db.scalars(stmt)
    return Page[ConversationRead](items=list(result.all()), total=total, limit=limit, offset=offset)


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    conversation_in: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    conversation = Conversation(**conversation_in.model_dump(), user_id=current_user.id)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    conversation = await _get_owned_conversation(conversation_id, current_user, db)
    await db.refresh(conversation, attribute_names=["messages"])
    return conversation


@router.put("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: int,
    conversation_in: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    conversation = await _get_owned_conversation(conversation_id, current_user, db)
    for field, value in conversation_in.model_dump(exclude_unset=True).items():
        setattr(conversation, field, value)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    conversation = await _get_owned_conversation(conversation_id, current_user, db)
    await db.delete(conversation)
    await db.commit()


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    message_in: ChatMessageCreate,
    current_user: User = Depends(enforce_chat_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEEPSEEK_API_KEY is not configured on the server.",
        )

    conversation = await _get_owned_conversation(conversation_id, current_user, db)

    return StreamingResponse(
        stream_agent_response(db, current_user, conversation, message_in.content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
