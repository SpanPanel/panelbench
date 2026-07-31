#!/usr/bin/env bash
#
# Developer bootstrap — reads .env and installs the local ebus-emitter checkout
# in editable mode into the simulator's venv.
#
# Why not pin the path in pyproject.toml? `ebus-emitter` is not on PyPI and each
# contributor's checkout lives at a different absolute path. The path is provided
# via the EBUS_EMITTER_PATH env var (loaded from .env) instead of being hardcoded.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env and edit the paths." >&2
    exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

if [[ -z "${EBUS_EMITTER_PATH:-}" ]]; then
    echo "ERROR: EBUS_EMITTER_PATH not set in .env" >&2
    exit 1
fi

if [[ ! -d "${EBUS_EMITTER_PATH}" ]]; then
    echo "ERROR: EBUS_EMITTER_PATH=${EBUS_EMITTER_PATH} does not exist." >&2
    exit 1
fi

echo "Syncing simulator deps (excluding ebus-emitter, which is local)…"
uv sync --group dev --no-install-package ebus-emitter

# Install the emitter's *locked* runtime deps, then the emitter itself with
# --no-deps. A bare `uv pip install --editable <path>` re-resolves the emitter's
# constraints against PyPI and ignores its uv.lock, so a fresh bootstrap can pull
# a transitive dependency the emitter was never tested against — that is how
# ebus-sdk 0.12.0 (whose Device constructor is incompatible with the 0.1.x API
# the emitter targets) landed in this venv and broke `span-simulator` at startup.
# Sourcing from the lock keeps this venv on exactly what the emitter pins.
echo "Installing ebus-emitter's locked runtime deps…"
uv export --project "${EBUS_EMITTER_PATH}" --no-dev --no-emit-project --no-hashes \
    | uv pip install --requirements -

echo "Installing ebus-emitter from ${EBUS_EMITTER_PATH} (editable, no re-resolution)…"
uv pip install --no-deps --editable "${EBUS_EMITTER_PATH}"

echo "Done. Verify with: uv run python -c 'import ebus_emitter; print(ebus_emitter.__file__)'"
