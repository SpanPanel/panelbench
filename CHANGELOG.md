# Changelog

## [unreleased] — emitter live-schema alignment and abstraction (feat/emitter-integration)

### Changed

- **Emitter schema alignment**: Adapter updated to work with emitter's live SPAN panel Homie 5 schema (flat node layout, accurate topology and properties).
- **Lugs IDs**: Updated to match emitter convention (`lugs-upstream`, `lugs-downstream`).
- **BESS/PV feeds**: Updated spec_generator to derive device feeds and metadata from circuit templates (stable circuit UUID linkage).
- **Simulator adapter**: Updated `spec_generator.py` and `runtime.py` to pass EVSE powers to emitter, set `clear_retained=True` on clone stop for graceful shutdown.

### Fixed

- **Type safety**: Fixed mypy error in simulator runtime (`_first_feed_for_device_type`) where template_name could be None.
