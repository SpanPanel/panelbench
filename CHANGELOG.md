# Changelog

## [unreleased] — emitter integration (feat/emitter-integration)

### Changed

- MQTT publishing and per-tick property emission moved into the external `ebus-emitter`
  package. The simulator now generates a `DeviceManifest` + `RuntimeSpec` from each
  clone profile via `span_panel_simulator.emitter_adapter` and drives the emitter via
  `Emitter.tick()`. UI / dashboard / HA-API / history / recorder integration paths that
  previously consumed legacy engine state are temporarily degraded; follow-up passes
  will migrate them to read `EbusPanelSnapshot` via `panel.last_snapshot`.
- `PanelInstance` no longer takes a shared `publish_fn`. Each panel owns its own
  per-clone `MqttClient` (constructed inside `emitter_adapter.runtime`).
- `app.py`'s central `/set` topic router has been removed. Each panel's emitter now
  subscribes to and dispatches its own `/set` topics via the `SetterRegistry`.

### Added

- `src/span_panel_simulator/emitter_adapter/` — runtime-spec generator
  (`spec_generator.py`), setter handlers (`setter_handlers.py`), per-clone runtime
  (`runtime.py`), profile loader (`profile_loader.py`), stable ID derivation
  (`instance_ids.py`).
- 16 emitter_adapter unit tests covering manifest construction, runtime-spec generation,
  setter handler registration, dashboard helpers.
- `scripts/dev-setup.sh` reads `.env` for `EBUS_EMITTER_PATH` and installs
  `ebus-emitter` editable from a developer-controlled checkout. `.env.example` and
  `.env` (gitignored) document the required variable.
- Dependency on `ebus-emitter` (local-path; not on PyPI).

### Removed

- `src/span_panel_simulator/publisher.py` — replaced by `ebus-emitter`'s wire layer.
- `tests/test_publisher.py`, `tests/test_schema.py` — exercised removed APIs.
- Legacy snapshot/publisher fixtures from `tests/conftest.py`.

### Deferred

- Full removal of `engine.py`, `circuit.py`, `clock.py`, `models.py`, `homie_const.py`,
  `schema.py`, `energy/` from the simulator. These modules still have non-MQTT consumers
  (HTTP bootstrap server uses `schema.render_for_panel`, `clone.py` uses `homie_const`
  type strings, HA-API endpoints read engine state). They will be cleaned up in
  follow-up passes once their consumers are migrated to the emitter-side equivalents
  (lifted into `ebus-emitter.scheduleRunner` and the emitter's vendored profiles).
- `tests/test_panel.py::test_start_and_stop` and `test_reload_restarts` and two
  `test_app.py` tests are skipped pending a broker fixture for the new emitter path.
- HA-API endpoints that previously read engine state return `None` post-cutover.
- Recorder data loading is no longer threaded into the panel; integration with the
  runtime spec is a follow-up.
