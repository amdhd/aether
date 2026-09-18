"""Security response headers for the API.

The SPA already gets a full set of these at the edge, from the CloudFront
response-headers policy in ``infra/terraform/layer1_persistent``. The API got
none of them: it is served from the ALB, which has no equivalent policy, and on
the Vercel + Render path there is no edge policy at all. So they live here
instead, where every deployment path picks them up rather than only the one that
happens to run behind CloudFront.

Raw ASGI, like ``app.core.body_limit``, rather than ``BaseHTTPMiddleware``. The
chat endpoint streams SSE for the length of a model turn; BaseHTTPMiddleware
relays a response through an anyio task group and stream, which is machinery a
long-lived stream does not need. Rewriting the header frame as it goes past is
all this has to do.
"""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

# One year, matching the CloudFront policy. includeSubDomains for the same
# reason it is set there: a subdomain served over plain HTTP can set cookies for
# the parent, so pinning the apex alone leaves that open.
HSTS_VALUE = "max-age=31536000; includeSubDomains"

# The API answers with JSON and nothing else — it has no markup, no scripts and
# no reason to be framed. 'none' across the board says exactly that, and matters
# because a browser that can be talked into *rendering* a response (an old
# content-type confusion, a bare error page) then has nothing to execute.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

_ALWAYS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


class SecurityHeadersMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        no_store_prefixes: tuple[str, ...] = (),
        send_csp: bool = True,
    ) -> None:
        self.app = app
        self.no_store_prefixes = no_store_prefixes
        self.send_csp = send_csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = list(_ALWAYS)

        if self.send_csp:
            extra.append((b"content-security-policy", API_CSP.encode()))

        if _is_https(scope):
            extra.append((b"strict-transport-security", HSTS_VALUE.encode()))

        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self.no_store_prefixes):
            extra.append((b"cache-control", b"no-store"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                overridden = {name for name, _ in extra}
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in overridden
                ]
                message = {**message, "headers": headers + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _is_https(scope: Scope) -> bool:
    """Whether the *client's* connection was over TLS.

    Both deployment targets terminate TLS at an edge and forward plain HTTP to
    the container, so the ASGI scheme is ``http`` in exactly the case where HSTS
    matters most. X-Forwarded-Proto carries the real answer, read under the same
    trust rule as ``rate_limit._client_ip``: only the last TRUSTED_PROXY_HOPS
    entries were written by infrastructure we control.

    A forged value is not a security problem here — a browser ignores HSTS from
    a connection that isn't already secure, so claiming https over http buys an
    attacker nothing. The gate is really about local development: sending HSTS
    from a dev server would pin localhost to https in the developer's browser
    for a year, and nothing in the app could unpin it.
    """
    if scope.get("scheme") == "https":
        return True
    if not settings.TRUST_PROXY_HEADERS:
        return False

    forwarded = Headers(scope=scope).get("x-forwarded-proto")
    if not forwarded:
        return False
    hops = [hop.strip().lower() for hop in forwarded.split(",") if hop.strip()]
    trusted = settings.TRUSTED_PROXY_HOPS
    if trusted >= 1 and len(hops) >= trusted:
        return hops[-trusted] == "https"
    return False
