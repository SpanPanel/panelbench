"""Every third-party module `src/` imports must be declared as a runtime dependency.

A dev environment installs the dev group, so the whole suite passes whether a
dependency is declared under `[project] dependencies` or under
`[dependency-groups] dev`. The add-on image does not: its Dockerfile runs
`pip install .`, which installs the project list and no groups. The difference is
invisible to every other test in this repo, and it is not hypothetical — it
shipped. `ebus-panel-sim` sat in the dev group while the emitter was vendored
under `src/panelbench/ebus_emitter/`, so nothing had to resolve it; de-vendoring
turned that into a runtime import of an undeclared distribution, and a bare
`pip install .` then failed at `import panelbench.app` with
`ModuleNotFoundError: No module named 'ebus_panel_sim'`.

Static rather than a build: this reads the source and the manifest, so it runs in
the same second as the rest of the suite and needs no network, no Docker and no
clean interpreter.

`if TYPE_CHECKING:` imports are deliberately excluded. They are never evaluated
at runtime, so a type-only dependency legitimately belongs in the dev group —
which is precisely why the check has to look at *where* an import sits rather
than merely whether the name appears.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "panelbench" if (_REPO / "panelbench" / "__init__.py").exists() else _REPO / "src"
_FIRST_PARTY = {"panelbench"}


def _runtime_imported_modules() -> dict[str, str]:
    """Top-level module name -> the file that imports it, outside TYPE_CHECKING."""
    found: dict[str, str] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _walk_runtime(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # `level > 0` is a relative import: first-party by construction.
                names = [node.module] if node.module and node.level == 0 else []
            else:
                continue
            for name in names:
                found.setdefault(name.split(".")[0], str(path.relative_to(_REPO)))
    return found


def _walk_runtime(tree: ast.AST) -> list[ast.AST]:
    """Every node except the bodies of `if TYPE_CHECKING:` blocks."""
    out: list[ast.AST] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            out.extend(_walk_runtime_body(node.orelse))
            continue
        out.append(node)
        out.extend(_walk_runtime(node))
    return out


def _walk_runtime_body(body: list[ast.stmt]) -> list[ast.AST]:
    out: list[ast.AST] = []
    for node in body:
        out.append(node)
        out.extend(_walk_runtime(node))
    return out


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _declared_runtime_distributions() -> set[str]:
    manifest = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set()
    for spec in manifest["project"]["dependencies"]:
        name = spec.split("[")[0]
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", ";"):
            name = name.split(sep)[0]
        declared.add(_normalize(name.strip()))
    return declared


def _normalize(name: str) -> str:
    """PEP 503: distribution names compare case-insensitively with -/_/. unified."""
    return name.lower().replace("_", "-").replace(".", "-")


def test_every_runtime_import_is_a_declared_dependency() -> None:
    imported = _runtime_imported_modules()
    declared = _declared_runtime_distributions()
    provided = packages_distributions()

    undeclared: list[str] = []
    for module, importer in sorted(imported.items()):
        if module in _FIRST_PARTY or module in sys.stdlib_module_names:
            continue
        dists = provided.get(module)
        if dists is None:
            # Not installed at all: this test cannot attribute it to a
            # distribution, and an import of something absent from the dev
            # environment is a different failure the suite already surfaces.
            continue
        if not any(_normalize(dist) in declared for dist in dists):
            undeclared.append(f"{module} (from {'/'.join(sorted(dists))}), imported by {importer}")

    assert not undeclared, (
        "these modules are imported at runtime by src/ but no distribution "
        "providing them is declared in [project] dependencies, so "
        "`pip install .` — which is how the add-on image builds — yields an "
        "install that fails on import:\n  " + "\n  ".join(undeclared)
    )
