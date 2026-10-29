import asyncio
import threading

import pytest
from fastapi import HTTPException

from app.core import password_work


async def test_password_work_runs_outside_event_loop_and_health_stays_responsive(client):
    loop_thread = threading.get_ident()
    started = threading.Event()
    release = threading.Event()

    def slow_password_check():
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(timeout=5)
        return True

    job = asyncio.create_task(password_work.run_password_work(slow_password_check))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        response = await asyncio.wait_for(client.get("/health"), timeout=1)
        assert response.status_code == 200
    finally:
        release.set()
        assert await job is True


async def test_password_pool_rejects_overload_and_recovers(monkeypatch):
    monkeypatch.setattr(password_work, "_slots", threading.BoundedSemaphore(1))
    started = threading.Event()
    release = threading.Event()

    def blocking():
        started.set()
        assert release.wait(timeout=5)

    job = asyncio.create_task(password_work.run_password_work(blocking))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        with pytest.raises(HTTPException) as caught:
            await password_work.run_password_work(lambda: None)
        assert caught.value.status_code == 503
        assert caught.value.headers["Retry-After"] == "1"
    finally:
        release.set()
        await job
    assert await password_work.run_password_work(lambda: "recovered") == "recovered"


async def test_password_pool_releases_slot_after_failure(monkeypatch):
    monkeypatch.setattr(password_work, "_slots", threading.BoundedSemaphore(1))

    def fail():
        raise ValueError("dummy failure")

    with pytest.raises(ValueError):
        await password_work.run_password_work(fail)
    assert await password_work.run_password_work(lambda: True)


async def test_cancelled_request_holds_capacity_until_worker_finishes(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(password_work, "_slots", slots)
    started, release = threading.Event(), threading.Event()

    def blocking():
        started.set()
        assert release.wait(timeout=5)

    job = asyncio.create_task(password_work.run_password_work(blocking))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        with pytest.raises(HTTPException):
            await password_work.run_password_work(lambda: None)
    finally:
        release.set()
        for _ in range(100):
            if slots.acquire(blocking=False):
                slots.release()
                break
            await asyncio.sleep(0.01)
    assert await password_work.run_password_work(lambda: True)
