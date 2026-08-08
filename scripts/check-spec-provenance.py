#!/usr/bin/env python3
"""Verify this repository's eBus specification provenance.

Two checks with deliberately different severities:

1. **Byte-identity at ``synced_commit`` — hard failure.** Every vendored capability
   catalog must be byte-identical to the specification's ``capabilities/`` at the
   commit ``.ebus-spec.json`` claims. This is what makes the base copies genuinely
   read-only: edit one and the build fails, which is the whole point, because a SPAN
   divergence belongs in the ``profiles/span/`` overlay where it is visible as a
   divergence rather than hidden inside a mirror.

2. **Drift against current spec — informational.** Run upstream's own
   ``tools/drift-report.py`` to report artifacts that have moved since we synced.
   Being behind is a legitimate state; not *knowing* is not. So this prints and
   never fails the build.

Usage:
    scripts/check-spec-provenance.py                     # $EBUS_SPEC_DIR, else clone to a temp dir
    scripts/check-spec-provenance.py --spec ~/spec-repo  # reuse a particular checkout

`EBUS_SPEC_DIR` belongs in `.env` (see `.env.example`); the flag overrides it.
Cloning works and needs no setup, so the variable is an optimisation — it saves a
network round trip per run — not a requirement. A path that no longer exists falls
back to cloning rather than failing, since that is the same situation as not
having set it.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / ".ebus-spec.json"
VENDORED_CATALOGS = REPO_ROOT / "spec/catalogs"


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False, **kwargs)  # type: ignore[call-overload,no-any-return]


def _spec_checkout(explicit: str | None, commit: str, workdir: pathlib.Path) -> pathlib.Path:
    """A specification checkout positioned at ``commit``.

    An explicit path is used in place rather than copied, so a local checkout is not
    moved off whatever ref the developer had; the comparison reads blobs out of git
    instead.
    """
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    dest = workdir / "specification"
    clone = _run(
        [
            "git",
            "clone",
            "--quiet",
            "https://github.com/electrification-bus/specification.git",
            str(dest),
        ]
    )
    if clone.returncode != 0:
        print(f"could not clone the specification repository:\n{clone.stderr}", file=sys.stderr)
        sys.exit(2)
    return dest


def _catalog_at_commit(spec: pathlib.Path, commit: str, name: str, out: pathlib.Path) -> bool:
    """Extract ``capabilities/<name>`` at ``commit`` without moving the checkout's HEAD."""
    show = _run(["git", "-C", str(spec), "show", f"{commit}:capabilities/{name}"])
    if show.returncode != 0:
        return False
    out.write_text(show.stdout)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        default=os.environ.get("EBUS_SPEC_DIR"),
        help="existing specification checkout (default: $EBUS_SPEC_DIR, else clone)",
    )
    args = parser.parse_args()

    # A stale path is the same situation as no path — the checkout is not there —
    # and cloning is the documented fallback, so fall back rather than failing on
    # a variable someone set months ago and a directory that has since moved.
    if args.spec and not pathlib.Path(args.spec).is_dir():
        print(f"EBUS_SPEC_DIR={args.spec} does not exist; cloning instead", file=sys.stderr)
        args.spec = None

    if not LOCKFILE.exists():
        print(f"no provenance lockfile at {LOCKFILE}", file=sys.stderr)
        return 2
    lock = json.loads(LOCKFILE.read_text())
    commit = lock["synced_commit"]

    ours = sorted(p for p in VENDORED_CATALOGS.glob("*.json"))
    if not ours:
        print(f"no vendored catalogs under {VENDORED_CATALOGS}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        spec = _spec_checkout(args.spec, commit, workdir)

        # Fetch defensively: an explicit checkout may predate the pinned commit.
        _run(["git", "-C", str(spec), "fetch", "--quiet", "origin"])

        print(f"provenance: {len(ours)} vendored catalogs vs specification @ {commit[:12]}")
        mismatched: list[str] = []
        for mine in ours:
            extracted = workdir / mine.name
            if not _catalog_at_commit(spec, commit, mine.name, extracted):
                mismatched.append(f"{mine.name}: absent from the specification at this commit")
                continue
            if not filecmp.cmp(mine, extracted, shallow=False):
                mismatched.append(f"{mine.name}: differs from the specification")

        if mismatched:
            print(
                "\nFAIL — vendored catalogs are not byte-identical to the spec:\n", file=sys.stderr
            )
            for m in mismatched:
                print(f"    {m}", file=sys.stderr)
            print(
                "\nBase copies are read-only. If this is a deliberate SPAN divergence it\n"
                "belongs in wire/profiles/span/ as an overlay, not as an edit to a mirror.\n"
                "If upstream moved, re-sync and update synced_commit in .ebus-spec.json.",
                file=sys.stderr,
            )
            return 1
        print("  ok — all byte-identical\n")

        # Informational: has the spec moved on since we synced?
        drift_tool = spec / "tools/drift-report.py"
        manifest = spec / "spec-manifest.json"
        if drift_tool.exists() and manifest.exists():
            print("drift against current specification (informational):")
            report = _run(
                [sys.executable, str(drift_tool), "--manifest", str(manifest), str(LOCKFILE)]
            )
            print(report.stdout or report.stderr)
        else:
            print("drift report skipped: upstream tooling not found in the checkout")

    return 0


if __name__ == "__main__":
    sys.exit(main())
