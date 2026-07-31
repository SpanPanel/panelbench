# Changelog

## 1.0.13 — 2026-07-31 — vendor the flat emitter

### Fixed

- **The HA add-on image could never start.** `ebus_emitter` is a hard, unconditional import
  (`app.py` → `emitter_adapter/runtime.py`), but the Dockerfile installs only
  `pip install --no-cache-dir .`, and the package was not a declared dependency — it is not
  on PyPI and was installed editable from `EBUS_EMITTER_PATH` by `scripts/dev-setup.sh`.
  The image therefore built successfully and failed at container start with
  `ModuleNotFoundError: No module named 'ebus_emitter'`. The same applied to anyone who
  cloned this repo and ran `uv sync` without `dev-setup.sh`. Vendoring removes the external
  dependency entirely, so both paths now work.

### Changed

- **The flat emitter is vendored at `src/span_panel_simulator/flat_emitter`**, copied from
  `ebus-emitter` 0.2.1 (commit `5b84de8`) — MIT, same copyright holders. The upstream repo
  has permanently diverged onto the parent/child (v1.0) Homie data model while this
  simulator continues to publish the flat schema, so the dependency delivered no upstream
  changes while costing path configuration, stale editable metadata, and an unsolvable
  distribution problem for the add-on. See the package docstring for full provenance.

  It also closes a correctness hazard: `clone.py` seeds energy accumulators against what
  this code publishes, and while the two lived in separate repos each side could look
  locally correct while jointly inverting circuit energy — which is exactly what happened.
  Both ends now sit in one repo under one test run.

- **The emitter's test suite came with it** (`tests/flat_emitter/`, 154 tests), including
  the circuit energy reference-frame regression tests. Total suite is now 395 tests.

- **`scripts/dev-setup.sh` is now a thin `uv sync` wrapper** and `.env.example` no longer
  defines `EBUS_EMITTER_PATH`; every dependency resolves from PyPI.

- **`ebus-sdk` is pinned to `==0.1.5`** rather than the range upstream declared, so that
  vendoring is behaviour-neutral: 0.1.5 is what the emitter's lockfile resolved and what
  this code was tested against. Letting it float within `<0.2` resolves 0.1.10, which drops
  the module-level `setLevel(INFO)` on the `homie` logger that `tests/test_main_logging.py`
  guards. Raising it is a deliberate follow-up, not a side effect of moving code.

- **`[tool.ruff.lint]` now declares `ignore = ["TC001", "TC002", "TC003"]`.** The existing
  comment already described this ignore, but the key was never present — none of the
  simulator's own modules happened to trigger the rules, so the omission was invisible.
  The vendored code was authored under an identical select list plus this ignore.

- **`ChargeMode` is exported from the vendored package** and used to annotate the
  `charge_mode` derivation in `engine.py` and `emitter_adapter/runtime.py`. Both sites
  already produced only valid values; mypy could not see it while `ebus_emitter` was an
  `ignore_missing_imports` module and `BESSConfig` was therefore `Any`.

## 1.0.12 — 2026-07-30 — emitter live-schema alignment and abstraction

### Changed

- **Emitter schema alignment**: Adapter updated to work with emitter's live SPAN panel Homie 5 schema (flat node layout, accurate topology and properties).
- **Lugs IDs**: Updated to match emitter convention (`lugs-upstream`, `lugs-downstream`).
- **BESS/PV feeds**: Updated spec_generator to derive device feeds and metadata from circuit templates (stable circuit UUID linkage).
- **Simulator adapter**: Updated `spec_generator.py` and `runtime.py` to pass EVSE powers to emitter, set `clear_retained=True` on clone stop for graceful shutdown.

### Fixed

- **Dev bootstrap dependency drift**: `scripts/dev-setup.sh` installed `ebus-emitter` with a bare `uv pip install --editable`, which re-resolves the emitter's dependency constraints against PyPI and ignores its `uv.lock`. A fresh bootstrap pulled `ebus-sdk` 0.12.0 — whose `Device` constructor is incompatible with the 0.1.x API the emitter targets — and panel startup died with `AttributeError: 'NoneType' object has no attribute 'get'` in `connect_broker()`. The script now installs the emitter's locked runtime dependencies first, then the emitter itself with `--no-deps`, so the venv matches what the emitter pins.
- **Type safety**: Fixed mypy error in simulator runtime (`_first_feed_for_device_type`) where template_name could be None.
