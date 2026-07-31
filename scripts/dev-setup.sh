#!/usr/bin/env bash
#
# Developer bootstrap.
#
# The flat emitter used to be an external checkout installed editable from
# EBUS_EMITTER_PATH. It is now vendored at `src/span_panel_simulator/flat_emitter`
# (see that package's docstring for provenance and rationale), so every dependency
# resolves from PyPI and this script is a thin wrapper over `uv sync`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "Syncing simulator deps…"
uv sync --group dev

echo "Done. Verify with: uv run python -c 'import span_panel_simulator.flat_emitter as e; print(e.__file__)'"
