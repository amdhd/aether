import json
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note
from app.models.task import Task, TaskPriority, TaskStatus
from app.models.user import User

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title of the task."},
                    "description": {"type": "string", "description": "Optional longer description."},
                    "due_date": {
                        "type": "string",
                        "description": "Due date in YYYY-MM-DD format, if any.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Priority of the task. Defaults to medium.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["todo", "doing", "done"],
                        "description": "Status of the task. Defaults to todo.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List the user's tasks, optionally filtered by status or priority.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["todo", "doing", "done"],
                        "description": "Only return tasks with this status.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Only return tasks with this priority.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Update an existing task by id. Only provided fields are changed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID of the task to update."},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD format."},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "status": {"type": "string", "enum": ["todo", "doing", "done"]},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Delete a task by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID of the task to delete."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "Create a new note for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note."},
                    "content": {"type": "string", "description": "Body content of the note."},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of tags.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "List all of the user's notes, most recently updated first.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Search the user's notes by title or content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for."},
                },
                "required": ["query"],
            },
        },
    },
]


def _serialize_task(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "priority": task.priority.value,
        "status": task.status.value,
    }


def _serialize_note(note: Note) -> dict[str, Any]:
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "tags": note.tags,
    }


async def _create_task(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    task = Task(
        user_id=user.id,
        title=args["title"],
        description=args.get("description"),
        due_date=date.fromisoformat(args["due_date"]) if args.get("due_date") else None,
        priority=TaskPriority(args["priority"]) if args.get("priority") else TaskPriority.medium,
        status=TaskStatus(args["status"]) if args.get("status") else TaskStatus.todo,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"task": _serialize_task(task)}


async def _list_tasks(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    stmt = select(Task).where(Task.user_id == user.id)
    if args.get("status"):
        stmt = stmt.where(Task.status == TaskStatus(args["status"]))
    if args.get("priority"):
        stmt = stmt.where(Task.priority == TaskPriority(args["priority"]))
    stmt = stmt.order_by(Task.created_at.desc())
    result = await db.scalars(stmt)
    return {"tasks": [_serialize_task(task) for task in result.all()]}


async def _update_task(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    task = await db.get(Task, args["task_id"])
    if task is None or task.user_id != user.id:
        return {"error": f"Task {args['task_id']} not found."}

    if "title" in args:
        task.title = args["title"]
    if "description" in args:
        task.description = args["description"]
    if "due_date" in args:
        task.due_date = date.fromisoformat(args["due_date"]) if args["due_date"] else None
    if "priority" in args:
        task.priority = TaskPriority(args["priority"])
    if "status" in args:
        task.status = TaskStatus(args["status"])

    await db.commit()
    await db.refresh(task)
    return {"task": _serialize_task(task)}


async def _delete_task(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    task = await db.get(Task, args["task_id"])
    if task is None or task.user_id != user.id:
        return {"error": f"Task {args['task_id']} not found."}

    await db.delete(task)
    await db.commit()
    return {"deleted": args["task_id"]}


async def _create_note(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    note = Note(
        user_id=user.id,
        title=args["title"],
        content=args.get("content", ""),
        tags=args.get("tags") or [],
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return {"note": _serialize_note(note)}


async def _list_notes(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    stmt = select(Note).where(Note.user_id == user.id).order_by(Note.updated_at.desc())
    result = await db.scalars(stmt)
    return {"notes": [_serialize_note(note) for note in result.all()]}


async def _search_notes(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    like = f"%{args['query']}%"
    stmt = (
        select(Note)
        .where(Note.user_id == user.id)
        .where(or_(Note.title.ilike(like), Note.content.ilike(like)))
        .order_by(Note.updated_at.desc())
    )
    result = await db.scalars(stmt)
    return {"notes": [_serialize_note(note) for note in result.all()]}


_TOOL_HANDLERS = {
    "create_task": _create_task,
    "list_tasks": _list_tasks,
    "update_task": _update_task,
    "delete_task": _delete_task,
    "create_note": _create_note,
    "list_notes": _list_notes,
    "search_notes": _search_notes,
}


async def call_tool(name: str, arguments: dict[str, Any], db: AsyncSession, user: User) -> str:
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})

    try:
        result = await handler(db, user, arguments)
    except (ValueError, KeyError) as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps(result)
