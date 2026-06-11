import json
import time
from datetime import date, datetime, timezone
from difflib import get_close_matches
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limit import check_tool_rate_limit
from app.models.note import Note
from app.models.task import Task, TaskPriority, TaskStatus
from app.models.user import User
from app.services import google_oauth

WEATHER_API_URL = "https://api.data.gov.my/weather/forecast"
WEATHER_CACHE_TTL_SECONDS = 3600
TAVILY_API_URL = "https://api.tavily.com/search"
CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3"

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
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Get the weather forecast for a town or city in Malaysia, using "
                "the data.gov.my public weather API. Forecast text is in Bahasa "
                "Melayu; translate or summarize it for the user as needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Name of a Malaysian town or city, e.g. 'Kuala Lumpur', 'Georgetown', 'Johor Bahru'.",
                    },
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for up-to-date information using Tavily.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_list_events",
            "description": (
                "List upcoming events on the user's primary Google Calendar. "
                "If the user hasn't connected Google Calendar, returns an error "
                "telling them to connect it from Settings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Max number of events to return (default 10, max 50).",
                    },
                    "time_min": {
                        "type": "string",
                        "description": "ISO 8601 datetime to list events from. Defaults to now.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Create a new event on the user's primary Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title."},
                    "description": {"type": "string", "description": "Optional event description."},
                    "location": {"type": "string", "description": "Optional event location."},
                    "start": {
                        "type": "string",
                        "description": "Start datetime in ISO 8601 format with timezone offset, e.g. 2026-06-15T09:00:00+08:00.",
                    },
                    "end": {
                        "type": "string",
                        "description": "End datetime in ISO 8601 format with timezone offset.",
                    },
                },
                "required": ["summary", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_delete_event",
            "description": "Delete an event from the user's primary Google Calendar by event id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "ID of the event to delete (from calendar_list_events).",
                    },
                },
                "required": ["event_id"],
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


_weather_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}


async def _fetch_weather_data() -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _weather_cache["data"]
    if cached is not None and now - _weather_cache["fetched_at"] < WEATHER_CACHE_TTL_SECONDS:
        return cached

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(WEATHER_API_URL, params={"contains": "Tn@location__location_id", "limit": 500})
        resp.raise_for_status()
        data = resp.json()

    _weather_cache["data"] = data
    _weather_cache["fetched_at"] = now
    return data


async def _get_weather(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    location = args["location"].strip().lower()

    try:
        forecasts = await _fetch_weather_data()
    except httpx.HTTPError as exc:
        return {"error": f"Could not reach the weather service: {exc}"}

    matches = [f for f in forecasts if f["location"]["location_name"].lower() == location]
    if not matches:
        matches = [f for f in forecasts if location in f["location"]["location_name"].lower()]

    if not matches:
        names = sorted({f["location"]["location_name"] for f in forecasts})
        suggestions = get_close_matches(args["location"], names, n=5)
        return {
            "error": f"No weather data found for '{args['location']}'.",
            "did_you_mean": suggestions or names[:5],
        }

    matches.sort(key=lambda f: f["date"])
    today = date.today().isoformat()
    forecast = next((f for f in matches if f["date"] >= today), matches[0])

    return {
        "location": forecast["location"]["location_name"],
        "date": forecast["date"],
        "min_temp_c": forecast["min_temp"],
        "max_temp_c": forecast["max_temp"],
        "morning_forecast": forecast["morning_forecast"],
        "afternoon_forecast": forecast["afternoon_forecast"],
        "night_forecast": forecast["night_forecast"],
        "summary": forecast["summary_forecast"],
        "language": "ms",
    }


async def _web_search(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    if not settings.TAVILY_API_KEY:
        return {"error": "Web search is not configured on the server."}

    if not check_tool_rate_limit(user.id, "web_search", settings.WEB_SEARCH_RATE_LIMIT_PER_MINUTE):
        return {"error": "Web search rate limit reached for this minute. Try again shortly."}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TAVILY_API_URL,
                json={
                    "api_key": settings.TAVILY_API_KEY,
                    "query": args["query"],
                    "max_results": 5,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"Web search request failed: {exc}"}

    results = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "content": item.get("content"),
        }
        for item in data.get("results", [])
    ]
    return {"query": args["query"], "results": results}


async def _calendar_list_events(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    access_token = await google_oauth.get_valid_access_token(db, user)
    if access_token is None:
        return {"error": "Google Calendar is not connected. Connect it from Settings."}

    if not check_tool_rate_limit(user.id, "calendar", settings.CALENDAR_RATE_LIMIT_PER_MINUTE):
        return {"error": "Calendar rate limit reached for this minute. Try again shortly."}

    max_results = min(max(int(args.get("max_results") or 10), 1), 50)
    params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max_results,
        "timeMin": args.get("time_min") or datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{CALENDAR_API_URL}/calendars/primary/events",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"Google Calendar request failed: {exc}"}

    events = [
        {
            "id": item.get("id"),
            "summary": item.get("summary"),
            "start": item.get("start"),
            "end": item.get("end"),
            "location": item.get("location"),
        }
        for item in data.get("items", [])
    ]
    return {"events": events}


async def _calendar_create_event(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    access_token = await google_oauth.get_valid_access_token(db, user)
    if access_token is None:
        return {"error": "Google Calendar is not connected. Connect it from Settings."}

    if not check_tool_rate_limit(user.id, "calendar", settings.CALENDAR_RATE_LIMIT_PER_MINUTE):
        return {"error": "Calendar rate limit reached for this minute. Try again shortly."}

    body: dict[str, Any] = {
        "summary": args["summary"],
        "start": {"dateTime": args["start"]},
        "end": {"dateTime": args["end"]},
    }
    if args.get("description"):
        body["description"] = args["description"]
    if args.get("location"):
        body["location"] = args["location"]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{CALENDAR_API_URL}/calendars/primary/events",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"Google Calendar request failed: {exc}"}

    return {
        "event": {
            "id": data.get("id"),
            "summary": data.get("summary"),
            "start": data.get("start"),
            "end": data.get("end"),
            "html_link": data.get("htmlLink"),
        }
    }


async def _calendar_delete_event(db: AsyncSession, user: User, args: dict[str, Any]) -> dict[str, Any]:
    access_token = await google_oauth.get_valid_access_token(db, user)
    if access_token is None:
        return {"error": "Google Calendar is not connected. Connect it from Settings."}

    if not check_tool_rate_limit(user.id, "calendar", settings.CALENDAR_RATE_LIMIT_PER_MINUTE):
        return {"error": "Calendar rate limit reached for this minute. Try again shortly."}

    event_id = quote(str(args["event_id"]), safe="")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{CALENDAR_API_URL}/calendars/primary/events/{event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"error": f"Google Calendar request failed: {exc}"}

    return {"deleted": args["event_id"]}


_TOOL_HANDLERS = {
    "create_task": _create_task,
    "list_tasks": _list_tasks,
    "update_task": _update_task,
    "delete_task": _delete_task,
    "create_note": _create_note,
    "list_notes": _list_notes,
    "search_notes": _search_notes,
    "get_weather": _get_weather,
    "web_search": _web_search,
    "calendar_list_events": _calendar_list_events,
    "calendar_create_event": _calendar_create_event,
    "calendar_delete_event": _calendar_delete_event,
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
