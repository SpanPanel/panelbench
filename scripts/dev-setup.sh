#!/usr/bin/env bash
#
# Developer bootstrap.
#
# The emitter is a pinned git dependency (`ebus-panel-sim`), not a checkout and no
# longer a vendored copy either. It ships its own capability catalogs and device
# profiles, which is what conformance measures against; `spec/catalogs` holds the
# byte-identical specification copies that `check-spec-provenance.py` verifies.
# So every dependency resolves from the lockfile and this is a thin `uv sync`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "Syncing simulator deps…"
uv sync --group dev

echo "Done. Verify with: uv run python -c 'import ebus_panel_sim as e; print(e.__file__)'"
