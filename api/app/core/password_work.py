"""Run expensive password operations off the event loop, with bounded admission."""
import asyncio
from collections.abc import Callable
from threading import BoundedSemaphore
from typing import TypeVar

from anyio import to_thread
from fastapi import HTTPException

T = TypeVar("T")
PASSWORD_WORKERS = 4
_slots = BoundedSemaphore(PASSWORD_WORKERS)


async def run_password_work(function: Callable[..., T], *args: object) -> T:
    # Reject overload instead of accumulating an unbounded queue of passwords
    # and request sessions. Unknown-account dummy checks use the same pool.
    if not _slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Sign-in service is busy. Try again shortly.",
                            headers={"Retry-After": "1"})
    slots = _slots
    work = asyncio.create_task(to_thread.run_sync(function, *args))

    def finished(task: asyncio.Task) -> None:
        slots.release()
        if not task.cancelled():
            task.exception()  # Retrieve failures even if the caller disconnected.

    work.add_done_callback(finished)
    # Shield the worker task so raw asyncio cancellation as well as ASGI
    # disconnects cannot release admission while bcrypt still occupies a thread.
    return await asyncio.shield(work)
