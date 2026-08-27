# Simulator — Agent Rules

Rules in this file apply to all AI coding agents working in this repository.

## No AI Attribution in Commits

Do **not** attribute work to Copilot, Claude, or any AI agent in commit messages. Commits represent human direction and decision-making; AI assists in
implementation but does not co-author.

**Rule:**

- Never include `Co-authored-by: Copilot` or any AI co-author trailer in commit messages.
- Never mention AI tools or attribution in commit messages.
- Commits belong to the human author directing the work.

This rule takes precedence over any default tool behavior that would add AI attribution.

## Energy System Encapsulation

The `panelbench.energy` package is the **sole authority** for all energy and power-flow calculations. This boundary was deliberately established to
replace scattered inline logic and must not be eroded.

**Rules:**

- The engine (`engine.py`) provides **raw measurements** to the energy module (PV power, load power, grid status). It must never pre-compute, resolve, or
  override energy scheduling, dispatch, or balance decisions.
- `PowerInputs` carries only observable state — never derived energy decisions like BESS scheduled state.
- All BESS scheduling (charge mode logic, TOU hour resolution, islanding overrides, forced-offline behavior) lives inside `EnergySystem.tick()` and `BESSUnit`.
  The engine must not call `resolve_scheduled_state()` or read `effective_state` to feed back into inputs.
- PV curtailment, GFE throttling, SOE enforcement, and bus balancing are energy-module concerns. The engine consumes `SystemState` results — it does not
  participate in producing them.
- New energy behaviors (e.g. demand response, rate optimization) must be added inside the energy package, not grafted onto the engine.

**Test discipline:** Tests drive BESS behavior through `BESSConfig` (charge_mode, charge_hours, discharge_hours), not by injecting state into `PowerInputs`.

## eBus / Homie Questions: Check the Specification First

This repository is an eBus **publisher**. Any question about what it must put on the
wire — topic layout, capability nodes, property names, enum domains, `$settable` /
`$format` / `$description` attributes, requester attribution, shed and islanding
behavior — is answered by the published specification, not from memory and not from a
migration guide.

**Source:** <https://github.com/electrification-bus/specification>
(local clone: `~/projects/ebus/specification`; `capabilities/`, `devices/`,
`data-models/`, `integration-guides/`, `registries/`)

**This repo already pins the spec.** `.ebus-spec.json` records `synced_commit` and the
per-capability versions in `implements`. Cite against that commit, not against whatever
your local clone happens to be on — `git -C ~/projects/ebus/specification log -1 <synced_commit>`
first, and if the clone is behind or ahead, say which commit you read.

**Rules:**

- Quote the relevant spec file with a `path:line` citation before asserting what the
  wire does. A claim about eBus behavior without a citation is a guess.
- The published specification **outranks** `~/projects/ebus/docs/ebus-schema-migration-guide.md`
  wherever the two disagree. The guide describes a flat-to-v1.0 mapping and has been
  wrong in practice; the spec is normative.
- Behavior that only the migration guide warrants belongs behind a vendor/variant gate,
  never in a shared code path that a spec-conformant consumer also reads.
- The vendored catalogs under `spec/catalogs/` are byte copies of the specification's
  `capabilities/` at `synced_commit`. Treat them as read-only — a SPAN divergence is
  carried in the profile overlay, never as an edit to a vendored catalog, so the byte
  comparison stays a valid check.
- Bumping `synced_commit` is its own change: re-vendor, re-verify the byte comparison,
  and update the `implements` pins in the same commit.
- If the spec is silent or self-contradictory, say so explicitly and open an issue
  upstream rather than inventing a rule.
