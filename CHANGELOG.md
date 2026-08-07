# Changelog

## 2.0.0 — parent/child eBus schema, in its own repository

Not yet published: no image is built for this repository, because no SPAN firmware publishes the v1.0 tree. The version is `2.0.0` because the wire format this
produces is incompatible with everything the `1.x` line published, not because a release is imminent.

### Changed

- **Split from `SpanPanel/simulator` into `SpanPanel/panelbench`.** The two publish incompatible schemas and a publisher cannot hot-load a wire format the way
  `span-panel-api` hot-loads a parser, so the parent/child work could never merge back — a branch that can never merge is a fork wearing a branch's clothes. The
  flat simulator keeps its name, its repository URL and its add-on slug, so no installed add-on is disturbed. History was carried across intact rather than
  squashed.
- **Renamed throughout to `panelbench`**: the Python package, the CLI entry point, the add-on directory and slug, the container image path, and the TLS and
  discovery hostname. The add-on directory has to match the slug, and the Supervisor derives a container's Docker DNS name from it, so those move together or
  not at all.

- **The emitter publishes the parent/child (v1.0) Homie data model** instead of the flat
  schema. Every circuit, BESS, PV, EVSE, lugs and MID is now its own Homie device with its
  own `$description` and `$state`, rather than a namespaced node hanging off the panel.
  Topics move from `ebus/5/<panel>/<circuit>/<prop>` to
  `ebus/5/<circuit>/<capability>/<prop>`.
- **The package is `ebus_emitter`**, renamed from `flat_emitter` — the old name described
  the schema rather than the component, so it would have needed renaming again at the next
  schema change.
- **`dominant-power-source` is split**, following the upstream migration. Its identity half
  is the MID's read-only `grid/grid-forming-entity`; its settable half is the panel's
  `shed/asserted-islanding-state` (`NONE` / `ON_GRID` / `OFF_GRID`). The assertion now
  drives load-shed treatment — auto-shed runs when the effective islanding state is not
  `ON_GRID` — where previously the override reached only a published value and influenced
  no decision. It overrides shed treatment only, never physics.
- **`<circuit>/info/name` is read-only.** There is no circuit rename over eBus in v1.0, so
  the settable-name handler is gone. The complete settable set is four topics: circuit
  `switch/relay`, circuit `load-shed/priority`, panel `shed/asserted-islanding-state`, and
  EVSE `config/user-max-charge-current`.
- **`ebus-sdk` moves to 0.17.0**, which carries two fixes this depends on: a transport-free
  root that accepts children, and a missing client logged at `debug` rather than `warning`
  when the tree is transport-free by design.

### Added

- **`.ebus-spec.json`**, the eBus specification provenance lockfile, declaring the spec
  commit this repository was reconciled against and the artifacts it implements. CI verifies
  every vendored capability catalog is byte-identical to the specification at that commit,
  so the base copies are read-only by enforcement rather than convention, and reports drift
  against the current spec.
- **The panel publishes `info/data-model-version`**, overridable from manifest metadata so a
  fixture can advertise a stale or future version and exercise a consumer's drift detection
  — something real firmware cannot be asked to do on demand.

### Fixed

- **Voltage properties published with no unit.** `_to_sdk_unit` resolved through a hand-written
  table that mapped `"V"` to a non-existent SDK enum member; it now resolves by enum value.
- **`set_property_value` assigned to `Property.coerced_value`**, which is a zero-arg getter —
  the assignment would have replaced the method rather than setting a value. Unreachable in
  practice, since the SDK has always exposed `set_value`.

## 1.0.14 — 2026-07-31 — vendor the flat emitter

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

- **The flat emitter is vendored at `src/panelbench/flat_emitter`**, copied from
  `ebus-emitter` 0.2.1 (commit `5b84de8`) — MIT, same copyright holders. The upstream repo
  has permanently diverged onto the parent/child (v1.0) Homie data model while this
  simulator continues to publish the flat schema, so the dependency delivered no upstream
  changes while costing path configuration, stale editable metadata, and an unsolvable
  distribution problem for the add-on. See the package docstring for full provenance.

  It also closes a correctness hazard: `clone.py` seeds energy accumulators against what
  this code publishes, and while the two lived in separate repos each side could look
  locally correct while jointly inverting circuit energy — which is exactly what happened.
  Both ends now sit in one repo under one test run.

- **The emitter's test suite came with it** (`tests/ebus_emitter/`, 154 tests), including
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

> **Version note.** This work merged as 1.0.13, the same version the circuit-energy fix
> below had already published. The add-on image tag is derived from `config.yaml`, so the
> second merge overwrote the first's image without changing the version — leaving anyone
> who had already pulled 1.0.13 on the earlier build with no update signal. Re-cut as
> 1.0.14 so Supervisor sees a change.

## 1.0.13 — 2026-07-31 — circuit energy reference frame

### Fixed

- **Clone energy seeds were read in the wrong reference frame.** `clone.py` seeded
  `initial_consumed_energy_wh` from a scraped panel's `imported-energy` and
  `initial_produced_energy_wh` from its `exported-energy`. The wire is enclosure-framed:
  `exported-energy` is energy the enclosure exported *to* a circuit (normal load
  consumption) and `imported-energy` is energy it imported *from* a circuit (backfeed).
  The two are now read the correct way round, in both the initial-translation path
  (`_translate_circuit`) and the refresh path (`update_config_from_scrape`).

  This mirrors the fix in `ebus-emitter` 0.2.1, which corrected the same inversion on the
  publish side. The two were previously wrong in a mutually cancelling way — clone read
  `imported-energy` into "consumed" and the emitter published "consumed" back out as
  `imported-energy` — so a cloned panel round-tripped its wire values faithfully while
  every value carried the wrong meaning. Correcting only one side would have broken the
  round-trip, so they move together.

- **Test fixtures encoded the same inversion.** `test_clone.py` gave a load circuit a
  rising `imported-energy` and a backfeeding solar circuit a rising `exported-energy`,
  which is the reverse of what a real panel publishes, and one fixture comment described
  positive `active-power` as "export" when on the wire it means the enclosure is importing
  from the circuit. Fixtures and the two energy-seeding test names now describe the
  enclosure frame.

### Requires

- **ebus-emitter >= 0.2.1**, which carries the matching publish-side fix. Pairing this
  release with an older emitter reinstates the inversion.

## 1.0.12 — 2026-07-30 — emitter live-schema alignment and abstraction

### Changed

- **Emitter schema alignment**: Adapter updated to work with emitter's live SPAN panel Homie 5 schema (flat node layout, accurate topology and properties).
- **Lugs IDs**: Updated to match emitter convention (`lugs-upstream`, `lugs-downstream`).
- **BESS/PV feeds**: Updated spec_generator to derive device feeds and metadata from circuit templates (stable circuit UUID linkage).
- **Simulator adapter**: Updated `spec_generator.py` and `runtime.py` to pass EVSE powers to emitter, set `clear_retained=True` on clone stop for graceful shutdown.

### Fixed

- **Dev bootstrap dependency drift**: `scripts/dev-setup.sh` installed `ebus-emitter` with a bare `uv pip install --editable`, which re-resolves the emitter's dependency constraints against PyPI and ignores its `uv.lock`. A fresh bootstrap pulled `ebus-sdk` 0.12.0 — whose `Device` constructor is incompatible with the 0.1.x API the emitter targets — and panel startup died with `AttributeError: 'NoneType' object has no attribute 'get'` in `connect_broker()`. The script now installs the emitter's locked runtime dependencies first, then the emitter itself with `--no-deps`, so the venv matches what the emitter pins.
- **Type safety**: Fixed mypy error in simulator runtime (`_first_feed_for_device_type`) where template_name could be None.
