# Vendored fidelity reference

These two files are **byte-identical copies** from the eBus emitter and must stay
that way. They are the reference half of the parity comparison: what a
spec-faithful producer publishes from a given config.

| File | Source |
| --- | --- |
| `forty_tab_minimal.yaml` | `examples/forty_tab_minimal.yaml` |
| `run_forty_tab_minimal.py` | `examples/run_forty_tab_minimal.py` |

- **Repository:** `electrification-bus/distribution-enclosure-simulator`
- **Commit:** `7fecdb30b62106da00d819aa9ab55adcaf1af980` (v0.3.3)
- **Examples last changed at:** `c4914e354490837b93b42d56b9ab3a6c628b8826`

## Why vendored rather than imported

`ebus-panel-sim` is a real runtime dependency of this package, so the
*emitter* needs no vendoring. But its wheel ships `packages = ["src/ebus_panel_sim"]`
only — `examples/` is not in the distribution, and `run_forty_tab_minimal.py` is
where the YAML-to-`DeviceManifest` reading lives.

That reading is part of the reference. Reimplementing it here would compare our
interpretation of the config against our emitter and call the result fidelity,
which is exactly the error the comparison exists to catch. So the file is copied
whole and the pure builders (`_build_manifest`, `_build_bess_config`, `_ticks`)
are called from the test.

The right end state is upstream exposing that builder as library API, at which
point this directory disappears. Until then, the copy is the honest option.

## Keeping it honest

`test_upstream_parity.py::test_vendored_reference_matches_upstream` compares
these files against a local checkout of the emitter when one is present
(`EBUS_EMITTER_CHECKOUT`, or the conventional sibling path), and skips when it is
not. So drift is caught on a developer machine and never silently tolerated,
while CI stays self-contained.

They are excluded from ruff and mypy in `pyproject.toml`: reformatting or
annotating them would destroy the only property that makes them useful.

## Re-syncing

Copy both files again, update the commit above, then run the parity test. A
changed gap set is the point — it means the reference moved, and the baseline in
`../parity_baseline.json` needs deliberate review rather than a blind refresh.
