# Vendored fidelity reference

These files are **byte-identical copies** of data the upstream emitter ships, for the version PanelBench pins. They are the reference half of the example
cell: a panel definition and the ticks that drive it.

| File                           | Source                                  |
| ------------------------------ | --------------------------------------- |
| `forty_tab_minimal.yaml`       | `examples/forty_tab_minimal.yaml`       |
| `forty_tab_minimal.ticks.yaml` | `examples/forty_tab_minimal.ticks.yaml` |

- **Repository:** `electrification-bus/distribution-enclosure-simulator`
- **Release:** `v0.10.0b2`, the version `pyproject.toml` pins (commit `296ac54c8704d1e6c0b49ee89ce2c5eea416fa63`)

## Why only these

The pinned release ships its reference captures, masked trees of real SPAN panels with their definitions and ticks, as package data, read through
`load_reference_capture`, so no copy of a capture is kept here. Its examples are not in its wheel, so these two files are copied. No upstream code is
copied: reading a definition or a captured tree is upstream's public API (`load_definition`, `load_ticks`, `load_reference_capture`,
`definition_from_tree`, `Emitter.from_definition`), so the reference is produced by the pinned package itself.

`test_vendored_example_matches_the_pinned_release` compares these bytes with the pinned release's tag in a local upstream checkout, and skips without one.
`.pre-commit-config.yaml` excludes them from every hook for the same reason. When the pin moves, re-copy every file from the new tag and re-review every
fidelity baseline.
