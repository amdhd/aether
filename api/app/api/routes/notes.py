from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_or_404
from app.db.session import get_db
from app.models.note import Note
from app.models.user import User
from app.schemas.common import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.note import NoteCreate, NoteRead, NoteUpdate

router = APIRouter(prefix="/notes", tags=["notes"])


async def _get_owned_note(note_id: int, user: User, db: AsyncSession) -> Note:
    return await get_owned_or_404(db, Note, note_id, user, "Note not found")


@router.get("", response_model=Page[NoteRead])
async def list_notes(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[NoteRead]:
    base_stmt = select(Note).where(Note.user_id == current_user.id)
    if q:
        like = f"%{q}%"
        base_stmt = base_stmt.where(or_(Note.title.ilike(like), Note.content.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
    stmt = base_stmt.order_by(Note.updated_at.desc()).limit(limit).offset(offset)
    result = await db.scalars(stmt)
    return Page[NoteRead](items=list(result.all()), total=total, limit=limit, offset=offset)


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
async def create_note(
    note_in: NoteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Note:
    note = Note(**note_in.model_dump(), user_id=current_user.id)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


@router.get("/{note_id}", response_model=NoteRead)
async def get_note(
    note_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Note:
    return await _get_owned_note(note_id, current_user, db)


@router.put("/{note_id}", response_model=NoteRead)
async def update_note(
    note_id: int,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Note:
    note = await _get_owned_note(note_id, current_user, db)
    for field, value in note_in.model_dump(exclude_unset=True).items():
        setattr(note, field, value)
    await db.commit()
    await db.refresh(note)
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    note = await _get_owned_note(note_id, current_user, db)
    await db.delete(note)
    await db.commit()
