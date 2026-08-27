# Spec conformance checking — design

**Status:** implemented, 2026-08-06. The rationale below is the record of why the tool is shaped the way it is; `DEVELOPER.md` §Spec Conformance is the entry
point for using it.

One correction since writing: the problem statement below says a unit the SDK cannot model is dropped. That was true of our code, not of the SDK —
`ebus_sdk.Unit` is a convenience vocabulary, not a constraint, and the SDK publishes a plain string unit verbatim. `_to_sdk_unit` now passes an unmodelled unit
through instead of discarding it. The abstract-token argument is unaffected: those name a dimension and must never reach the wire.

## The problem

We vendor the eBus capability catalogs and pin them in `.ebus-spec.json`, and `scripts/check-spec-provenance.py` proves in CI that all 15 are byte-identical to
the specification at `synced_commit`. That check is sound and it passes.

It also could not have caught the defect that prompted this document.

`conventions/property-json.md` defines an **abstract unit token**: four properties (`soc`'s `soe`, `total-energy-storage`, `loadup-headroom`, and `info`'s
`nameplate-capacity`) carry `"unit": "energy"` in the catalog, naming a dimension rather than a unit, and a publisher **MUST** substitute a concrete unit before
publishing. Our vendored copies of those catalogs are byte-perfect. Our lockfile is truthful. And our wire layer maps a catalog unit onto the SDK's `Unit` enum,
which models concrete Homie units only, then drops anything it cannot model (`ebus_emitter/wire/graph_builder.py`). `Unit("energy")` raises, the exception is
swallowed, and the property publishes **with no `$unit` at all** — silently.

We are not emitting that today, but only by luck: `wire/profiles/bess.json` happens to override `soe` and `nameplate-capacity` to `kWh` in the overlay. Nothing
in the code knows the token is special. Composing `total-energy-storage` or `loadup-headroom` — both legal, both in the catalog we already vendor — triggers it.

Stated generally:

> The lockfile proves we copied the right bytes. Nothing proves we understood them.

Source-side syncing — vendoring, pinning, byte-compare, drift reporting — is structurally incapable of catching this class, because the defect is in
interpretation, and interpretation lives in code. The only thing that can catch it is checking what we actually published.

## Why this matters more for a simulator than for most publishers

The simulator's entire value is being a faithful reference producer. Consumers are developed against it; a subtly non-conformant tree does not stay ours, it
propagates into everything built against it. A simulator that is wrong in a way nobody notices is worse than no simulator.

The specification's own usage guide says, of the simulator case:

> Because it consumes the spec JSON directly, a simulator cannot drift from the spec.

That assumption is what this tool exists to stop trusting. It is not true for the four abstract-token properties, and the general form — "consuming the data
correctly is automatic" — is exactly the assumption that failed.

## Scope

**In:** every rule decidable from a single snapshot of the published Homie 5 `$description` documents plus the vendored catalogs and profiles.

Homie 5 makes this small. Each device publishes one retained `$description` JSON document composed by the SDK, carrying its nodes and, per property, `datatype`
/ `unit` / `format` / `settable` / `name`. Every rule below reads only those documents — no property value topics, no time series.

**Out, deliberately:**

- Property _values_ — range conformance, enum membership. Needs the value topics.
- Temporal rules — monotonicity of cumulative registers. Needs a capture window, not a snapshot.
- Live-broker operation. See "Feeds" below.
- Anything about the HTTP API, the dashboard, or clone/scrape.

## Architecture

Three units. The first two know nothing about SPAN or MQTT and are the reusable core.

```text
src/panelbench/conformance/model.py            $description documents -> typed tree
src/panelbench/conformance/catalogs.py         vendored capability catalogs as data
src/panelbench/conformance/device_profiles.py  vendored device profiles as data
src/panelbench/conformance/rules.py            (tree, catalogs, profiles) -> [Finding]
src/panelbench/conformance/report.py           aggregate + render text and JSON
src/panelbench/conformance/feeds.py            thin adapters producing documents
scripts/check-conformance.py                             CLI, mirroring check-spec-provenance.py
```

Two unlike names on purpose: an eBus **device profile** is upstream's data saying which capabilities a device type composes (`device_profiles.py`); the
**conformance report** is our output (`report.py`).

The package sits beside `ebus_emitter/` rather than inside it: it validates the emitter's output and must not be able to import from it, or the boundary below
is unenforceable.

**`model.py`** parses description documents into devices → nodes → properties. Knows the Homie 5 document shape. No eBus vocabulary, no catalogs.

**`rules.py`** is the rule set as pure functions over the model. Knows eBus. Has no notion of where the tree came from.

**`feeds.py`** produces the documents:

- `from_devices(devices)` — calls `device.description()` on the built SDK tree. No broker, no capture, runs inside the existing test suite in milliseconds.
  Catches defects at authoring time.
- `from_capture(path)` — reads a dump of retained `$description` topics. Proves the wire matches what we composed, so it catches lifecycle and transport defects
  the in-process feed cannot see.

Both feeds are a few lines each and share every rule. A live-broker mode is deliberately not built: it drags credentials and a running broker into CI, is
non-deterministic, and welds the tool to one transport — which is the opposite of the property that makes the core reusable.

**Enforced boundary:** `model.py` and `rules.py` import nothing from `panelbench` and nothing MQTT-related. A test asserts this. That constraint is what would
let the core be offered upstream later as a publisher-side conformance checker, which is a category the specification's own `tools/` does not currently have —
all four scripts there are author-side.

## The conformance floor

The obvious design — check the published tree against the catalogs and fail on any mismatch — is wrong, and `framework.md` §Conformance Latitude says so
directly:

> The specification is deliberately permissive: it describes what a device **can** and **should** publish, and **how**, rather than a fixed set every device
> must publish… It **MAY** publish properties this specification does not define, and it **MAY** publish a property with a wider or differently-partitioned
> datatype or value set than a capability catalog recommends… The one standing condition is self-description: a publisher advertises the shape it actually
> publishes in its Homie 5 `$description` and `$format`, so any consumer can interpret it.
>
> …The capability catalogs and data-model documents… are a recommendation and a reference, **not a checklist a conformant device must match.**

So the floor is a single obligation — **be self-describing** — and it is small on purpose. `req` in a device profile is "capability-level conformance
_guidance_", default MAY. `req` on a catalog property is "the spec's guidance, not a per-device requirement". Publishing is opt-in (principle 3). A datatype may
be widened or differently partitioned (principle 10). The spec's own `check-property-catalogs.py` emits "only **advisory** notes (never fatal)… none constrains
what a conformant publisher may do."

That leaves very little that can honestly be called a violation, and it changes what the tool is for. A validator that gates on catalog match would be enforcing
a checklist the specification explicitly says does not exist — it would fail conformant publishers and fight the design.

## What the tool actually produces

Not a pass/fail gate but a **conformance profile**: every published property classified into exactly one bucket, plus a small set of genuine violations.

| Bucket         | Meaning                                                       | Output   |
| -------------- | ------------------------------------------------------------- | -------- |
| **Match**      | id in the catalog, datatype/unit/format compatible            | counted  |
| **Divergence** | id in the catalog, shape differs — legal under principle 10   | reported |
| **Extension**  | id in no catalog — legal, self-description carries it         | reported |
| **Omission**   | catalog property this device does not publish — legal, opt-in | counted  |
| **Violation**  | breaks Homie 5, or one of eBus's few real MUSTs               | ERROR    |

The first four are the valuable output and none of them fails a build. That profile is the contract a consumer codes against: it states exactly what this
producer emits, which parts are shared vocabulary, and which are ours. It is derived from the wire, so it cannot drift from what we actually publish — which is
precisely what prose integration notes always do.

This is also what makes the tool useful beyond our own SDK. Nothing in the classification depends on how the tree was built, in what language, or over what
transport. Any eBus publisher gets a profile; any Homie publisher gets at least the Homie-level violations.

## Severity model

- **ERROR** — violates Homie 5, or an explicit eBus MUST. Exit non-zero.
- **ADVISORY** — a divergence or extension worth seeing. Never fails.
- **counted** — appears in the summary only.

## Rules

### Violations — the only rules that fail a build

| #   | Rule                                                                                  | Basis                                                             |
| --- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| V1  | An `enum` or `color` property carries a `$format`                                     | Homie 5 marks `format` required for exactly these two datatypes   |
| V2  | `$unit` is never an abstract unit token                                               | `property-json.md`: "`energy` is never published on the wire"     |
| V3  | A property whose catalog entry carries an abstract token publishes a concrete `$unit` | `property-json.md`: "A publisher MUST substitute a concrete unit" |
| V4  | Every device publishes a parseable `$description`                                     | The standing self-description condition                           |

V2 and V3 are the pair that motivated this document, and they are complementary: V2 catches the failure the convention predicts (publishing the literal string),
V3 catches ours (publishing nothing). Neither publisher needs to know the token vocabulary — the rule derives it from the vendored catalog.

V3 is worth being precise about, because Homie itself would permit it: Homie's `unit` is optional and free-form, so a unitless float is Homie-legal. It is eBus
that imposes the MUST, and only for properties whose catalog entry carries an abstract token. That is the whole justification for the rule, and it should be
stated in the finding text so nobody later "fixes" it by relaxing it.

### Observations — reported, never fatal

| #   | Observation                                                                                                     | Bucket     |
| --- | --------------------------------------------------------------------------------------------------------------- | ---------- |
| O1  | A node `$type` matching `energy.ebus.capability.*` names no registered capability                               | Divergence |
| O2  | A published `datatype` differs from the catalog's                                                               | Divergence |
| O3  | `$unit` differs from the catalog's concrete unit                                                                | Divergence |
| O4  | `$settable` differs from the catalog or overlay, except a conditionally-settable property narrowed to read-only | Divergence |
| O5  | `$unit` present on a non-numeric datatype                                                                       | Divergence |
| O6  | A published property id absent from its capability's catalog                                                    | Extension  |
| O7  | A node whose `$type` is not an `energy.ebus.capability.*` at all                                                | Extension  |
| O8  | A capability the profile names but the device does not publish                                                  | Omission   |
| O9  | A catalog property the device does not publish                                                                  | Omission   |

O1 is deliberately not an error: the spec's own tool treats an unregistered capability type as an advisory note, and matching upstream's severity matters more
than our intuition about it. O2 likewise — principle 10 permits "a wider or differently-partitioned datatype", which is broad enough that almost any divergence
is legal so long as it is self-described.

O4 carries one exception, because the catalog's `settable` field cannot express everything the specification's Settable column says. `capabilities/switch.md`
gives `relay` a Settable of "when `relay-controllable`", and `relay-controllable` is per circuit, so a panel with a locked circuit is _required_ to publish a
mix: `$settable` present on the controllable circuits and absent on the locked ones. The vendored `switch.json` flattens that to `"settable": true` and keeps
the condition in prose, so comparing against the field alone puts a divergence row on every faithful clone of a real panel — noise that teaches people to stop
reading the report. `rules._CONDITIONALLY_SETTABLE` names the conditional properties, with the citation, and O4 skips only the one direction the condition can
explain: a property the catalog marks settable, published read-only. Declaring a read-only property settable still diverges, and so does dropping `$settable`
from an unconditionally settable property.

`load-shed/priority` is deliberately not in that set even though this producer can lock it per circuit. Its Settable column reads plain `yes`; what the spec
grants is publisher-level latitude ("declining to be writable is a permitted deviation"), and a permitted deviation is exactly what a divergence row is for. The
per-circuit version of that lock rests on the migration guide, which does not outrank the specification.

O8 and O9 exist to make coverage visible, not to complain. A consumer author wants to know which catalog surface this producer actually implements; that is a
fact about the producer, not a defect. They are counted in the summary and listed only under `--verbose`, since a full BESS tree omits a great many optional
properties and listing them all by default would bury V1–V4.

## Composition with existing tooling

```text
check-spec-provenance.py    specification bytes  ->  vendored bytes
check-conformance.py        vendored bytes       ->  published wire
```

Run together, the chain is specification → wire with no prose step. Neither script needs network or a spec checkout at runtime: provenance clones only when
explicitly asked, and conformance reads the vendored artifacts.

## Testing

- **Golden fixture.** A committed set of `$description` documents from a known-good tree, asserted to produce zero violations.
- **Golden profile.** The same fixture's full classification — match / divergence / extension / omission counts — committed and asserted. This is the stronger
  test: it fails when our published surface changes in _any_ way, including a change that is perfectly legal. A legal change should be a deliberate, reviewed
  edit to the expected profile, not something that slips through because no rule happened to cover it.
- **One broken fixture per violation rule**, asserting that rule fires exactly once and no other. Mutating the golden fixture is enough to build these; they
  need no simulator run.
- **In-process check in the existing suite.** The `from_devices` feed runs against the real built tree, so every test run validates conformance rather than
  deferring it to a separate job.
- **A targeted regression guard for the trap that prompted this**: assert that every vendored catalog property carrying an abstract unit token is either not
  composed into any profile, or is composed with a concrete unit override. That fires the moment someone adds `total-energy-storage` to the BESS profile without
  a unit.

## Error handling

- A `$description` that is missing or does not parse is a hard error naming the device, never a silent skip. A checker that quietly validates nothing is worse
  than no checker.
- A node typed as a capability we have not vendored is an ERROR, not a lookup miss: it means we published something outside the set we pinned.
- Zero findings exits 0 with a one-line summary, matching the shape `check-spec-provenance.py` already prints.

## Future work — release-to-release deltas

A conformance profile describes one published tree. Two profiles diffed describe **what changed between two releases**, which is a separate need this design
happens to satisfy for free.

The motivating artifact is the hand-written v1.0 entity and configuration delta document kept in the integration's docs. Its risk is not that it is wrong but
that it is _incomplete_ — a change nobody noticed does not appear in it, and the first report comes from a user whose history broke.

Splitting that honestly:

- **Mechanically derivable from two profiles:** properties and nodes that appeared or disappeared, datatypes that changed, units that changed, settability that
  changed, topology that changed. This is the enumeration, and completeness is exactly what a machine is good for.
- **Not derivable:** the severity ranking, which is a judgment about user impact; the justification, which is a product decision; and the mapping from wire
  properties to user-visible entity ids, which needs the consumer's naming rules and therefore belongs on the consumer side, not here.

So the tool would supply a complete change enumeration that a human then ranks and justifies. That is the right division: the enumeration is where omissions
hide, and the ranking is where judgment belongs.

Worth noting this is not speculative. Both trees exist side by side today — the flat worktree and the parent/child worktree publish the same panel configuration
through the same broker port, which is what the `simupgrade` / `simdowngrade` harness was built for. Capturing a profile from each is the same operation twice.

**A related upstream thread, deliberately not pursued here.** Some deltas exist because an upstream schema decision renamed or removed something rather than
aliasing it — the `battery` → `soc` key was removed outright, and a v1.0 property was split into two successors. Whether eBus has, or wants, a deprecation or
alias policy is a real question for a specification whose consumers carry years of user history on stable identifiers. It is orthogonal to this tool and should
be raised on its own, with evidence, once the profile diff can show concretely what such a policy would have to cover.

## Open questions

- Whether `from_capture` lands in the first implementation or immediately after. The in-process feed delivers most of the value and has no infrastructure cost;
  the capture feed is what proves the wire and what generalises to another publisher.
- Whether O4 should compare `$settable` against the base catalog or against the SPAN overlay. Comparing against the overlay reports nothing, since the overlay
  is what we publish from. Comparing against the base catalog surfaces every deliberate SPAN divergence — `shed/policy` read-only, `circuit info/name`
  non-settable — which is arguably the more useful profile, since those divergences are exactly what a consumer needs to know about this producer.
- Whether the conformance profile should be emitted as JSON as well as text. If it is the contract a consumer codes against, a machine-readable form is the
  point; if it is only a developer report, text is enough. Leaning JSON, since the integration is the consumer and could assert against it.
