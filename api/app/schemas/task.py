from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.task import TaskPriority, TaskStatus


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    due_date: date | None = None
    priority: TaskPriority = TaskPriority.medium
    status: TaskStatus = TaskStatus.todo


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    due_date: date | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None


    @field_validator('title', 'priority', 'status', mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        # Validators run for supplied fields; omitted defaults stay excluded
        # from model_dump(exclude_unset=True).
        if value is None:
            raise ValueError("This field cannot be null")
        return value


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
