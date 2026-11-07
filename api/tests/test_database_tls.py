"""The deployed API must verify the database it talks to.

asyncpg's default sslmode, "prefer", encrypts when the server offers TLS but
checks no certificate, so anything that can answer on the database address is
trusted with every query. These pin the three pieces that make verification
happen: the URL asks for verify-full, the driver actually receives it, and the
image ships the CA bundle the driver checks against.
"""

from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

REPO = Path(__file__).resolve().parents[2]


def test_the_rds_url_requires_full_verification() -> None:
    outputs = (REPO / "infra/terraform/modules/rds/outputs.tf").read_text()
    url_line = next(line for line in outputs.splitlines() if "postgresql+asyncpg://" in line)
    assert url_line.rstrip().rstrip('"').endswith("?ssl=verify-full")


def test_asyncpg_receives_verify_full() -> None:
    url = make_url("postgresql+asyncpg://u:p@db.example.internal:5432/aether?ssl=verify-full")
    engine = create_async_engine(url)
    _, kwargs = engine.dialect.create_connect_args(url)
    assert kwargs["ssl"] == "verify-full"


def test_the_image_ships_and_points_at_the_rds_ca_bundle() -> None:
    dockerfile = (REPO / "api/Dockerfile").read_text()
    add = dockerfile[dockerfile.index("ADD --checksum=sha256:"):]
    add = add[: add.index("\nENV PGSSLROOTCERT=")]
    assert "truststore.pki.rds.amazonaws.com" in add, "the RDS CA bundle must be added with a pinned checksum"
    bundle_path = add.split()[-1]
    assert f"ENV PGSSLROOTCERT={bundle_path}\n" in dockerfile
