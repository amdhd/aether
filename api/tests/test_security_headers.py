"""The API's own security response headers.

The SPA has had these since the CloudFront policy was written; the API never
did, on any deployment path. These pin the set and, more importantly, pin the
conditions — HSTS only over TLS, no-store only where tokens travel.
"""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.security_headers import API_CSP, HSTS_VALUE, SecurityHeadersMiddleware

VERCEL_JSON = Path(__file__).resolve().parents[2] / "web" / "vercel.json"
CLOUDFRONT_TF = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "terraform"
    / "layer1_persistent"
    / "main.tf"
)


async def _plain_ok(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _get(app, path: str = "/", headers: dict[str, str] | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, headers=headers)


async def test_the_always_on_headers_are_present(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_auth_responses_are_not_storable(client: AsyncClient) -> None:
    """These bodies carry access tokens. Nothing else marks them uncacheable,
    and 'no cache headers' is not the same as 'no cache will store it'."""
    resp = await client.post("/api/v1/auth/login", json={"email": "no@one.test", "password": "x"})
    assert resp.headers["cache-control"] == "no-store"


async def test_no_store_is_not_applied_to_everything(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert "cache-control" not in resp.headers


async def test_hsts_is_withheld_from_a_plain_http_request() -> None:
    """A dev server sending HSTS would pin localhost to https in the developer's
    browser for a year, and nothing in the app could unpin it."""
    resp = await _get(SecurityHeadersMiddleware(_plain_ok))
    assert "strict-transport-security" not in resp.headers


async def test_hsts_is_sent_when_the_edge_reports_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both deploy targets terminate TLS at an edge and forward plain HTTP, so
    the ASGI scheme is 'http' in exactly the case where HSTS matters."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)

    resp = await _get(SecurityHeadersMiddleware(_plain_ok), headers={"x-forwarded-proto": "https"})
    assert resp.headers["strict-transport-security"] == HSTS_VALUE


async def test_a_forwarded_proto_is_ignored_when_proxies_are_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)

    resp = await _get(SecurityHeadersMiddleware(_plain_ok), headers={"x-forwarded-proto": "https"})
    assert "strict-transport-security" not in resp.headers


async def test_only_the_hop_our_own_edge_wrote_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client can prepend to X-Forwarded-Proto, so the leftmost entry is the
    caller's to choose. Same rule as rate_limit._client_ip: count from the right."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)

    resp = await _get(SecurityHeadersMiddleware(_plain_ok), headers={"x-forwarded-proto": "https, http"})
    assert "strict-transport-security" not in resp.headers


async def test_csp_can_be_suppressed_where_the_docs_are_served() -> None:
    """Swagger UI is a real HTML page that loads its own scripts and styles, so
    default-src 'none' would break it wherever it is enabled."""
    with_csp = await _get(SecurityHeadersMiddleware(_plain_ok, send_csp=True))
    assert with_csp.headers["content-security-policy"] == API_CSP

    without = await _get(SecurityHeadersMiddleware(_plain_ok, send_csp=False))
    assert "content-security-policy" not in without.headers


async def test_headers_reach_a_response_that_never_hits_a_route(client: AsyncClient) -> None:
    """The body limit rejects from raw ASGI, before routing. That 413 is exactly
    the kind of response an inner middleware would miss, which is why this one is
    added last and so wraps the others."""
    resp = await client.post(
        "/api/v1/auth/login",
        content=b"x" * (settings.MAX_REQUEST_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"


async def test_an_existing_header_is_replaced_not_duplicated() -> None:
    async def _claims_sniffable(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-content-type-options", b"sniff-away")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    resp = await _get(SecurityHeadersMiddleware(_claims_sniffable))
    assert resp.headers.get_list("x-content-type-options") == ["nosniff"]


# --- the two edges must not drift apart ---------------------------------------


def _directives(csp: str) -> dict[str, str]:
    return {
        part.split(" ", 1)[0]: part.split(" ", 1)[1] if " " in part else ""
        for part in (p.strip() for p in csp.split(";"))
        if part
    }


def test_the_spa_csp_matches_the_one_cloudfront_serves() -> None:
    """The SPA is served by CloudFront on the AWS path and by Vercel on the
    other. The whole finding behind this file was that the two paths had
    different header sets; a CSP tightened in one place and not the other is the
    same bug wearing a different hat.
    """
    tf = CLOUDFRONT_TF.read_text()
    block = tf.split("content_security_policy = join(\"; \", [", 1)[1].split("])", 1)[0]
    cloudfront = _directives("; ".join(line.strip().strip(',"') for line in block.splitlines() if '"' in line))

    vercel_headers = json.loads(VERCEL_JSON.read_text())["headers"][0]["headers"]
    vercel = _directives(
        next(h["value"] for h in vercel_headers if h["key"] == "Content-Security-Policy")
    )

    assert set(vercel) == set(cloudfront)
    for name, value in cloudfront.items():
        if name == "connect-src":
            # Terraform interpolates the real API origin here when it knows it;
            # vercel.json is static and carries the same fallback the Terraform
            # uses when it does not.
            assert vercel[name] == "'self' https:"
            continue
        assert vercel[name] == value, f"{name} differs between the two edges"


def test_the_spa_gets_the_same_transport_headers_as_the_api() -> None:
    vercel_headers = {
        h["key"].lower(): h["value"] for h in json.loads(VERCEL_JSON.read_text())["headers"][0]["headers"]
    }
    assert vercel_headers["strict-transport-security"] == HSTS_VALUE
    assert vercel_headers["x-content-type-options"] == "nosniff"
