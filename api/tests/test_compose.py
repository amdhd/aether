"""docker-compose.yml must start the services it declares.

The web service runs the Vite dev server (`npm run dev`), so it has to be built
from a Node stage of web/Dockerfile. Built from the default (last) stage it got
the nginx runtime image, which has no npm, and `docker compose up` failed.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _stages(dockerfile: Path) -> dict[str, str]:
    stages = {}
    for line in dockerfile.read_text().splitlines():
        match = re.match(r"FROM\s+(\S+)\s+AS\s+(\S+)", line, re.IGNORECASE)
        if match:
            stages[match.group(2)] = match.group(1)
    return stages


def test_the_web_dev_server_is_built_from_a_node_stage() -> None:
    web = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["web"]
    assert web["command"][0] == "npm"
    build = web["build"]
    assert isinstance(build, dict), "web must name a build target; the last stage is nginx"
    stages = _stages(REPO / build["context"] / "Dockerfile")
    assert stages[build["target"]].startswith("node:")
