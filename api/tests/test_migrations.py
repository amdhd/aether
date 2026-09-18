"""Guards on the Alembic history itself.

The test suite builds its schema with ``Base.metadata.create_all``, which is fast
and keyless but never runs a single migration. So the migration chain — the thing
both deploy paths actually execute — had no coverage at all, and a history that
could not be upgraded reached main without one test going red.

That is exactly what happened: two pull requests written in parallel each added a
revision on top of ``b7e2d5f9c3a1`` and merged independently, leaving two heads.
Nothing conflicted in the schema, but ``alembic upgrade head`` refuses to choose
between heads, so `render.yaml`'s preDeployCommand and the ECS migration task in
deploy.yml both failed before touching the database.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))


def test_the_history_has_exactly_one_head(script_directory: ScriptDirectory) -> None:
    """Two parallel pull requests adding migrations is the ordinary way to get
    here, and the failure is invisible until a deploy runs. When this goes red,
    the fix is `alembic merge -m "..." <head> <head>`."""
    heads = script_directory.get_heads()
    assert len(heads) == 1, (
        f"alembic has {len(heads)} heads ({', '.join(heads)}); "
        "`alembic upgrade head` cannot choose between them and every deploy will fail"
    )


def test_every_revision_is_reachable_from_the_head(script_directory: ScriptDirectory) -> None:
    """A revision orphaned by a bad down_revision would never run, so the column
    it adds silently never exists in production."""
    head = script_directory.get_current_head()
    reachable = {rev.revision for rev in script_directory.iterate_revisions(head, "base")}
    everything = {rev.revision for rev in script_directory.walk_revisions()}

    assert everything - reachable == set()


def test_the_chain_upgrades_from_empty_to_head(tmp_path: Path) -> None:
    """The command both deploy paths run, run end to end.

    A subprocess with its own DATABASE_URL, rather than alembic's Python API in
    process: the app's env is already loaded here and alembic's env.py builds its
    own engine and event loop, so driving it in-process would test a setup
    neither deploy path uses.
    """
    db = tmp_path / "migrations.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
            # The production validator refuses to boot on placeholder secrets,
            # and env.py imports settings.
            "ENVIRONMENT": "development",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"
    assert db.exists()
