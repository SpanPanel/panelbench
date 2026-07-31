# Changelog

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
