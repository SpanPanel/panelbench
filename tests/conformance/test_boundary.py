"""The conformance package must not depend on this simulator, or on any transport.

The rules describe *any* eBus publisher's output. A single convenience import from the
emitter would silently make them describe only ours.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/panelbench/conformance")
FORBIDDEN_ROOTS = {"panelbench", "ebus_sdk", "aiomqtt", "paho"}


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        # A relative import (level > 0) stays inside the package.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


def test_package_imports_nothing_forbidden() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        bad = _imported_roots(path) & FORBIDDEN_ROOTS
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "conformance package must stay independent of the simulator and of any "
        f"transport, but found: {offenders}"
    )
