# Vendored fidelity reference

These files are **byte-identical copies** of data the upstream emitter ships, for the version PanelBench pins. The two example files are the reference half
of the example cell: a panel definition and the ticks that drive it.

| File                           | Source                                       |
| ------------------------------ | -------------------------------------------- |
| `forty_tab_minimal.yaml`       | `examples/forty_tab_minimal.yaml`            |
| `forty_tab_minimal.ticks.yaml` | `examples/forty_tab_minimal.ticks.yaml`      |
| `main32_r202639-tree-v1.json`  | `tests/fixtures/main32_r202639-tree-v1.json` |

`main32_r202639-tree-v1.json` is the release's masked capture of a real SPAN panel on release 202639 or later: the reference half of the captured cell.
Upstream masked it before shipping it, and PanelBench reads it as published.

- **Repository:** `electrification-bus/distribution-enclosure-simulator`
- **Release:** `v0.9.0`, the version `pyproject.toml` pins (commit `2dbddf7c507776a08e03a64825454972696d23df`)

## Why only data

Upstream's examples and test fixtures are not in its wheel, so the files are copied. No upstream code is copied: reading a definition or a captured tree
is upstream's public API (`load_definition`, `load_ticks`, `tree_from_snapshot`, `definition_from_tree`, `Emitter.from_definition`), so the reference is
produced by the pinned package itself.

`test_vendored_example_matches_the_pinned_release` compares these bytes with the pinned release's tag in a local upstream checkout, and skips without one.
`.pre-commit-config.yaml` excludes them from every hook for the same reason. When the pin moves, re-copy every file from the new tag and re-review every
fidelity baseline.
