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

The `panelbench.energy` package is the **sole authority** for all energy and power-flow calculations. This boundary was deliberately established to replace
scattered inline logic and must not be eroded.

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

## Topology Comes From What a Panel Publishes, Never From a Reading

A panel's structure is decided from what its tree publishes: its devices, their `connection` records, their roles, and which properties the panel publishes at
all. A power, current or energy value is a reading. It seeds accumulators, fills ticks and history, and computes flows. It never decides structure. A clone or
import of a panel captured at night must describe the same panel as one captured at noon.

**Rules:**

- Which device carries which flow, which circuit is solar or a battery's breaker, whether an inverter or battery is hybrid, and where a DER sits relative to the
  lugs are decided from the tree's structure. `clone.py` does this today:
  - a circuit's energy mode comes from what it feeds (`_device_role_to_mode`);
  - a commissioned system comes from `connection/feeds-device-type` (`_commissioned_system`);
  - a battery's position comes from which circuit or lugs name it.
- A threshold on a reading ("at least 1 W", "producing", "drawing") never gates such a decision. A few watts at night are real production, and a standby draw
  is real too. A sign or a magnitude at the captured instant says nothing about what the hardware is.
- Whether a property is published is structure; its value is a reading. A `0.0` the panel publishes is not an unpublished property.
- A device's `info` nameplate values (a rating, a model, a hardware version) describe what it is and do not vary with the instant of capture, so they are
  structure, not readings.
- Booking a tick's flows by a reading's sign, as the emitter's `span` variant books every producing circuit as solar, computes a flow. It is allowed, because
  it decides nothing that persists.
- Where the tree does not say, the config says so explicitly (a default the clone notes), rather than guessing from a reading.

## eBus / Homie Questions: Check the Specification First

This repository is an eBus **publisher**. Any question about what it must put on the wire — topic layout, capability nodes, property names, enum domains,
`$settable` / `$format` / `$description` attributes, requester attribution, shed and islanding behavior — is answered by the published specification, not from
memory and not from a migration guide.

**Source:** <https://github.com/electrification-bus/specification> (local clone: `~/projects/ebus/specification`; `capabilities/`, `devices/`, `data-models/`,
`integration-guides/`, `registries/`)

**This repo already pins the spec.** `.ebus-spec.json` records `synced_commit` and the per-capability versions in `implements`. Cite against that commit, not
against whatever your local clone happens to be on — `git -C ~/projects/ebus/specification log -1 <synced_commit>` first, and if the clone is behind or ahead,
say which commit you read.

**Rules:**

- Quote the relevant spec file with a `path:line` citation before asserting what the wire does. A claim about eBus behavior without a citation is a guess.
- The published specification **outranks** `~/projects/ebus/docs/ebus-schema-migration-guide.md` wherever the two disagree. The guide describes a flat-to-v1.0
  mapping and has been wrong in practice; the spec is normative.
- Behavior that only the migration guide warrants belongs behind a vendor/variant gate, never in a shared code path that a spec-conformant consumer also reads.
- The vendored catalogs under `spec/catalogs/` are byte copies of the specification's `capabilities/` at `synced_commit`. Treat them as read-only — a SPAN
  divergence is carried in the profile overlay, never as an edit to a vendored catalog, so the byte comparison stays a valid check.
- Bumping `synced_commit` is its own change: re-vendor, re-verify the byte comparison, and update the `implements` pins in the same commit.
- If the spec is silent or self-contradictory, say so explicitly and open an issue upstream rather than inventing a rule.
