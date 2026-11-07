"""API docs leak the full API surface, so only local environments serve them."""

import pytest

from app.main import app, docs_enabled


@pytest.mark.parametrize("environment", ["production", "staging", "prodution", ""])
def test_docs_are_off_outside_development_and_test(environment: str) -> None:
    assert docs_enabled(environment) is False


@pytest.mark.parametrize("environment", ["development", "test"])
def test_docs_are_on_locally(environment: str) -> None:
    assert docs_enabled(environment) is True


async def test_this_test_environment_still_serves_docs(client) -> None:
    # Sanity check that the app under test (ENVIRONMENT=development by default)
    # still exposes the schema, so the switch above is what decides it.
    assert app.openapi_url == "/openapi.json"
    assert (await client.get("/openapi.json")).status_code == 200
