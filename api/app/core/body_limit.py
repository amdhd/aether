"""Refuse an oversized request body before it is buffered.

FastAPI reads and parses the request body *before* it solves dependencies, so
on the chat endpoint the rate limit and the monthly cost cap only run once an
upload has already been consumed and spooled to disk. Nothing else bounded it
either: the ALB does not, and the WAF's ``SizeRestrictions_BODY`` rule is
deliberately set to Count so legitimate ~200 KB CSV attachments aren't blocked.
The transfer itself was therefore unbounded, and the guards that were supposed
to be "the real gate" all sat downstream of it.

This is that gate, as raw ASGI so it runs ahead of routing, body parsing and
every dependency. Two checks, because either alone is bypassable: a declared
Content-Length is rejected without reading a byte, and the body is counted as
it streams for requests that don't declare one (or lie about it).
"""

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_TOO_LARGE_JSON = b'{"detail":"Request body is too large."}'


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._declares_oversized_body(scope):
            await self._reject(send)
            return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    # Cut the body off here rather than reading the remainder
                    # onto disk. The app unwinds on the disconnect, and its own
                    # response — whatever it would have been — is dropped below
                    # in favour of a 413.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if rejected:
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except ClientDisconnect:
            # Ours, from the cut above; a genuine disconnect can't reach here
            # because `rejected` would still be False.
            if not rejected:
                raise

        if rejected:
            await self._reject(send)

    def _declares_oversized_body(self, scope: Scope) -> bool:
        declared = Headers(scope=scope).get("content-length")
        if declared is None:
            return False
        try:
            return int(declared) > self.max_bytes
        except ValueError:
            # Malformed header: don't guess. The streaming count still applies.
            return False

    async def _reject(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_TOO_LARGE_JSON)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _TOO_LARGE_JSON})
