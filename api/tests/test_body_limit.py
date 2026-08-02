from typing import Any, AsyncIterator

from httpx import AsyncClient

from app.core.body_limit import MaxBodySizeMiddleware
from app.core.config import settings


async def test_declared_oversized_body_is_refused_without_reading_it() -> None:
    """The Content-Length path exists to answer before touching the body at all
    — the streaming counter would otherwise read up to the limit first. Driven
    at the ASGI layer because a well-behaved HTTP client won't send a
    Content-Length that disagrees with what it transmits.
    """
    called = False
    received: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True

    async def receive() -> Any:  # pragma: no cover - must never be awaited
        received.append(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/register",
        "headers": [(b"content-length", str(settings.MAX_REQUEST_BYTES + 1).encode())],
    }
    await MaxBodySizeMiddleware(app, max_bytes=settings.MAX_REQUEST_BYTES)(scope, receive, send)

    assert sent[0]["status"] == 413
    assert not called, "the app ran despite an oversized Content-Length"
    assert not received, "the body was read despite an oversized Content-Length"


async def test_normal_request_is_unaffected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "small@example.com", "name": "Small", "password": "supersecret123"},
    )
    assert resp.status_code == 201


async def test_declared_oversized_body_is_refused(client: AsyncClient) -> None:
    """The realistic shape: any normal client sets Content-Length, so this is
    settled before a single byte of body is read."""
    resp = await client.post(
        "/api/v1/auth/register", content=b"x" * (settings.MAX_REQUEST_BYTES + 1)
    )
    assert resp.status_code == 413
    assert resp.json()["detail"] == "Request body is too large."


async def test_oversized_body_never_reaches_the_endpoint(client: AsyncClient) -> None:
    """A 413 rather than the 422 the route would return proves the request was
    stopped in front of routing, not merely rejected by validation afterwards."""
    resp = await client.post(
        "/api/v1/auth/register",
        headers={"Content-Type": "application/json"},
        content=b'{"junk":"' + b"x" * (settings.MAX_REQUEST_BYTES + 1) + b'"}',
    )
    assert resp.status_code == 413


async def test_streamed_body_without_content_length_is_refused(client: AsyncClient) -> None:
    """A Content-Length check alone is trivially bypassed by chunking, which is
    the whole point of also counting the body as it arrives."""

    async def oversized_chunks() -> AsyncIterator[bytes]:
        chunk = b"x" * 64_000
        for _ in range((settings.MAX_REQUEST_BYTES // len(chunk)) + 2):
            yield chunk

    resp = await client.post("/api/v1/auth/register", content=oversized_chunks())
    assert resp.status_code == 413


async def test_body_just_under_the_limit_still_reaches_the_route(client: AsyncClient) -> None:
    """Guards the other direction: the cap must not clip legitimate uploads,
    which is why it sits well above MAX_ATTACHMENT_BYTES."""
    body = b'{"padding":"' + b"x" * (settings.MAX_REQUEST_BYTES - 1000) + b'"}'
    assert len(body) < settings.MAX_REQUEST_BYTES
    resp = await client.post(
        "/api/v1/auth/register", headers={"Content-Type": "application/json"}, content=body
    )
    # Reached the route and failed *its* validation — not the size gate.
    assert resp.status_code == 422
