from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_or_404
from app.db.session import get_db
from app.models.task import Task
from app.models.user import User
from app.schemas.common import Page
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])

# A kanban board is expected to show the user's whole task list at once, so
# default to a high limit; pagination is still available for users with an
# unusually large number of tasks.
DEFAULT_LIMIT = 200
MAX_LIMIT = 200


async def _get_owned_task(task_id: int, user: User, db: AsyncSession) -> Task:
    return await get_owned_or_404(db, Task, task_id, user, "Task not found")


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[TaskRead]:
    base_stmt = select(Task).where(Task.user_id == current_user.id)
    total = await db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
    stmt = base_stmt.order_by(Task.created_at.desc()).limit(limit).offset(offset)
    result = await db.scalars(stmt)
    return Page[TaskRead](items=list(result.all()), total=total, limit=limit, offset=offset)


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_in: TaskCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    task = Task(**task_in.model_dump(), user_id=current_user.id)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    return await _get_owned_task(task_id, current_user, db)


@router.put("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: int,
    task_in: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    task = await _get_owned_task(task_id, current_user, db)
    for field, value in task_in.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    task = await _get_owned_task(task_id, current_user, db)
    await db.delete(task)
    await db.commit()
