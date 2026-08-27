# Vendored fidelity reference

These two files are **byte-identical copies** from the eBus emitter and must stay
that way. They are the reference half of the parity comparison: what a
spec-faithful producer publishes from a given config.

| File | Source |
| --- | --- |
| `forty_tab_minimal.yaml` | `examples/forty_tab_minimal.yaml` |
| `run_forty_tab_minimal.py` | `examples/run_forty_tab_minimal.py` |

- **Repository:** `electrification-bus/distribution-enclosure-simulator`
- **Commit:** `171bb94f0960ccd2f62282c83ec203017bd6aa7f` (tagged `v0.8.0`, whose annotated tag object is `ed67c849`)
- **Examples last changed at:** `8d6c34ac9453781d9171d0941e91d84caa39be9f`

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

Record the **commit**, not the tag object: upstream's release tags are annotated, so
`git rev-parse v0.8.0` hands back the tag's own SHA and not the commit it points at.
Use `git rev-parse v0.8.0^{commit}`. The v0.5.1 line this replaced recorded
`8735c24c`, which is that release's tag object rather than its commit `b561d069` —
an id that resolves to something real, and so reads as correct, while naming the
wrong kind of object.

The v0.3.3 → v0.5.1 re-sync moved `run_forty_tab_minimal.py` only, and only its
annotations: `Literal["self-consumption", "backup-only"]` became the `ChargeMode`
alias the emitter now exports from its root, which is the same type spelled once.
`forty_tab_minimal.yaml` did not move at all. `parity_baseline.json` was
reviewed and left unchanged — it still matches, which is the expected result for
a reference whose published surface did not move, and is the evidence that it
did not.

The v0.5.1 → v0.8.0 re-sync moved both files, and this time the reference's
published surface moved with them. `forty_tab_minimal.yaml` gains a `pool_pump`
template commissioned `never-backup: true` at tab 3, and
`run_forty_tab_minimal.py` writes the matching manifest key — so the reference
now publishes a circuit whose `load-shed/priority` carries no `$settable`, which
is the second commissioning lock 0.8.0 introduced. The `solar` template is
unchanged and still reads `priority: NEVER` on a non-controllable relay: 0.8.0
fixed how that combination is *interpreted* (`NEVER` is an ordinary settable
value, not the lock), not the example that expresses it.

Both parity baselines were reviewed and left unchanged. They still match — the
new circuit is published identically by both producers, `$settable` absence
included — which is the evidence that panelbench picked up the new key rather
than that nothing was compared.
