"""Guards on render.yaml that the app's own code decides.

A deploy manifest can drift out of agreement with the application silently:
nothing imports it, no test touches it, and the mistake only shows up as a
service that never reports healthy. These pin the two settings whose correct
value is determined by application code rather than by preference.
"""

from pathlib import Path

import pytest
import yaml

from app.main import app

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


@pytest.fixture(scope="module")
def api_service() -> dict:
    manifest = yaml.safe_load(RENDER_YAML.read_text())
    return next(svc for svc in manifest["services"] if svc["name"] == "aether-api")


def test_health_check_path_is_a_route_the_app_serves(api_service: dict) -> None:
    """The health check must point at a route that exists *in production*.

    This previously pointed at /docs, which app.main disables whenever
    ENVIRONMENT=production — exactly the environment render.yaml pins. Render's
    check therefore 404'd forever and the service never went healthy.
    """
    health_path = api_service["healthCheckPath"]

    # Mounted sub-routers carry no `path` of their own; the health endpoints are
    # declared on the app itself, so a plain scan of top-level routes covers them.
    served = {path for route in app.routes if (path := getattr(route, "path", None))}
    assert health_path in served, f"{health_path} is not a route this app serves"

    # The docs routes are the specific trap: they exist in dev and vanish in
    # production, so a passing local check says nothing about the deployment.
    assert health_path not in {"/docs", "/redoc", "/openapi.json"}


def test_proxy_headers_are_trusted_behind_renders_edge(api_service: dict) -> None:
    """Render terminates TLS at its edge, so the socket peer is that proxy.

    Left at the default (False), _client_ip falls back to the socket peer and
    every user in the world shares one per-IP rate-limit bucket — the auth
    limiter silently stops being per-client.
    """
    env = {var["key"]: var.get("value") for var in api_service["envVars"]}
    assert env.get("TRUST_PROXY_HEADERS") == "true"
    # One Render proxy in front of the container = the client IP is the last hop.
    assert env.get("TRUSTED_PROXY_HOPS") == "1"
