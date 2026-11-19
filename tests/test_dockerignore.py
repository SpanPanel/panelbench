"""An image built from a working checkout carries only what a build from git does.

The add-on's Dockerfile copies ``configs/`` whole. A developer checkout also holds the
``ref_`` templates generated at every start, user clones, their history databases,
``configs/.secrets/`` and ``configs/rates/`` (API keys), none of which git tracks, and
without ``.dockerignore`` a local build copies them all into the image. This reads the
file as Docker does: rules in order, the last matching one deciding, a rule matching a
path or any directory above it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

from tests._helpers import SHIPPED_TEMPLATES

_REPO: Final = Path(__file__).resolve().parents[1]


def _rules() -> list[tuple[bool, re.Pattern[str]]]:
    rules: list[tuple[bool, re.Pattern[str]]] = []
    for raw in (_REPO / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line.removeprefix("!").strip("/")
        regex = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*", "[^/]*")
        rules.append((negated, re.compile(f"{regex.replace(r'\\?', '[^/]')}$")))
    return rules


def _excluded(path: str) -> bool:
    parts = path.split("/")
    candidates = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    excluded = False
    for negated, pattern in _rules():
        if any(pattern.match(candidate) for candidate in candidates):
            excluded = not negated
    return excluded


@pytest.mark.parametrize(
    "path",
    [
        "configs/ref_MAIN_32.yaml",
        "configs/ref_MAIN_40.yaml",
        "configs/main32_clone.yaml",
        "configs/ref_MAIN_32_TANDEMS_history.db",
        "configs/.last_config",
        "configs/.secrets/sim-001.yaml",
        "configs/rates/rates_cache.yaml",
        ".env",
        ".git/config",
        ".venv/bin/python",
        ".local/certs/ca.key",
        "src/panelbench/__pycache__/app.cpython-314.pyc",
    ],
)
def test_what_git_does_not_track_stays_out_of_the_image(path: str) -> None:
    assert _excluded(path)


@pytest.mark.parametrize(
    "path",
    [
        *(f"configs/{name}" for name in SHIPPED_TEMPLATES),
        "configs/presets/presets.yaml",
        "pyproject.toml",
        "src/panelbench/app.py",
        "mosquitto/mosquitto.conf.template",
        "panelbench/run.sh",
    ],
)
def test_what_the_dockerfile_copies_goes_in(path: str) -> None:
    assert not _excluded(path)


def test_every_tracked_config_goes_in() -> None:
    """So a template added to ``configs/`` and tracked is not silently left out."""
    tracked = subprocess.run(
        ["git", "ls-files", "configs"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    assert tracked
    assert [path for path in tracked if _excluded(path)] == []
