# Changelog

## 2.5.2 — discovery hands over an address, not an internal alias

### Fixed

- **Discovery now gives Home Assistant the panel's address rather than an internal add-on hostname**, which Home Assistant writes over the address on a panel
  you already added and which no certificate names, so a panel added by IP stopped verifying.

## 2.5.1 — the address a client can actually verify us by

An existing install corrects itself on the next start: a stored certificate that names the wrong address is already treated as unfit and re-signed, so nothing
needs clearing by hand.

### Fixed

- **The certificate SAN and the mDNS advertisement now carry this host's own address rather than its upstream router's**, which under `host_network: true` left
  no address a client could reach the panel at and verify the certificate against.
- **Supervisor discovery entries are now removed when the add-on stops**, where the registration's identifier was silently discarded and stale entries
  accumulated across restarts.
- **The add-on image builds again**, where a dependency bump had pinned a version of the eBus SDK that the emitter's own requirements exclude, leaving the
  two unsatisfiable together.

## 2.5.0 — a fixed certificate authority, so a firmware upgrade looks like one

**The certificate authority is now shipped with the package rather than generated at startup**, and is identical to the one the simulator ships. The simulator
emulates SPAN firmware before r202633 and PanelBench emulates r202633 and later, so stopping one and starting the other rehearses a firmware upgrade on a single
panel — the hand-carried config brings the serial across, and the panel keeps its address and ports. Every install used to mint its own authority, so the swap
presented a new trust anchor and read as a panel substitution to anything pinned to the old one. A firmware upgrade does not rotate a panel's certificate
authority, and now neither does the swap.

Its SHA-256, the value Home Assistant pins and displays, is `3cf8c14a78900b8736870c95adcc931cdcb3a51bc3029c96efafd0a4cb790d97`.

**Coming from the simulator: upgrade the simulator to 1.1.0 first, then swap.** Replacing a generated authority with the shipped one raises one "SPAN Panel
certificate authority changed" repair, and doing it at the simulator's own upgrade means the swap itself raises none — which is the point of the exercise.
Swapping straight from an older simulator instead raises that one repair at the swap; it is expected, and the fingerprint it displays is the value above.

The authority's private key is committed deliberately and is not a leaked secret. It signs nothing that chains to a real panel, which mints its own authority in
firmware and is pinned per config entry, and a real panel ever reporting the fingerprint above would be conclusive evidence of tampering.

### Fixed

- **A changed advertised address or container hostname now re-signs only the server certificate**, where it used to regenerate the certificate authority along
  with it and present a pinned consumer with a trust anchor that had rotated for no reason.
- **A server certificate signed by a superseded authority is detected by signature rather than by issuer name**, which is the only way to tell two of these
  authorities apart: every authority PanelBench ever generated carries the same subject and none carries a key identifier.
- **An expired or nearly expired server certificate is re-signed at startup** instead of being served until a handshake fails against an anchor that never
  changed — a failure a pinned consumer correctly reports as retryable and then retries forever.
- **A corrupt or unreadable server certificate is replaced rather than raised out of startup**, where it previously put the add-on into a restart loop.
- **An advertised address that is not an IP address is ignored with a warning** instead of aborting certificate generation.
- **Certificate files are written atomically**, so a container killed mid-write cannot leave a truncated certificate behind.
- **The authority's private key is no longer written into the certificate directory**, and one left there by an earlier build is removed; nothing reads it, and
  it was world-readable in a directory that survives upgrades.

## 2.4.0 — the two commissioning locks a real panel publishes

`ebus-panel-sim` 0.8.0 finishes what 0.7.0 started: a SPAN panel commissions circuits with two independent locks, and both are published only as the *absence*
of a Homie `$settable` attribute. A relay-locked circuit offers no relay command; a never-backup circuit offers no shed-priority change. Neither has a value of
its own on the wire, so anything that read values alone reproduced neither — which is exactly how a clone of a real panel ended up offering controls the panel
it was cloned from does not.

### Changed

- **Locked circuits no longer advertise a relay command**: the three the shipped 40-space default commissions `non-controllable` now publish `switch/relay`
  without `$settable`, report `switch/relay-requester: CONFIGURATION` at rest instead of `NONE`, and accept no `/set`, which is what real firmware has always
  published.
- **The dashboard refuses a relay toggle on a locked circuit** and no longer offers a clickable status dot for one, where it used to answer `{"ok": true}` and
  zero the circuit's power behind a published tree saying the relay is closed and not commandable.
- **The dashboard refuses a shed-priority change on a never-backup circuit** and disables the control in the edit form, because such a circuit is commissioned
  permanently `OFF_GRID` and saving another value produced a config the panel could not start from.
- **The conformance report no longer flags a locked relay as a divergence**, since the specification makes `switch/relay` settable "when `relay-controllable`"
  and every faithful clone of a real panel was publishing a row per locked circuit for behaviour the specification requires.
- **A property the catalog marks settable unconditionally still diverges** when it is published read-only, so the narrowing is scoped to the one condition the
  specification states.

### Added

- **Cloning a panel reproduces its never-backup circuits**: a captured circuit whose `load-shed/priority` carries no `$settable` clones to `never_backup: true`
  and the clone publishes the same absence, mirroring the relay lock clones already carried.
- **A source panel that locks the priority at anything but `OFF_GRID` is contradicting itself**, so the clone keeps the published priority, drops the lock, and
  logs why rather than writing a config that cannot start.

### Fixed

- **The conformance report describes what the simulator actually publishes again**: it had credited every circuit and both lugs devices with a
  `connection/count` property the emitter stopped declaring in 0.6.0, which went unnoticed for two releases because the property never carried a value and so
  was invisible to the topic-set comparison that guards the capture.

## 2.3.0 — panels serve TLS, and say where

A simulated panel published a CA it never used. `/api/v2/certificate/ca` handed out a certificate authority, the bootstrap server listened in plaintext and
nothing else listened at all, so the anchor pointed at a door that did not exist.

That was invisible until the integration started pinning before it registers. Its flow fetches the CA over the plaintext port, then refuses to trust it unless
it validates the certificate the panel actually serves — a check with no way past, because the next thing it sends is the panel passphrase. Against a
simulator with no TLS listener the check could only fail, so setup dead-ended on `ca_leaf_mismatch` no matter what the user typed. Hardware serves both; the
simulator now does too.

### Added

- **Every panel serves its REST API over TLS as well as plaintext**, on a port 1000 above its bootstrap one (8081 -> 9081), using the same certificate bundle
  whose CA `/api/v2/certificate/ca` publishes. Both listeners share one application, so the two ports cannot answer differently. A bind failure on either now
  stops the server rather than leaving the half that bound holding its port — the caller's response to `EADDRINUSE` is to retry on the next pair, which a
  surviving listener would defeat.
- **Discovery publishes both ports.** Supervisor records carry `https_port` beside `port`, and mDNS carries an `httpsPort` TXT record beside `httpPort`. The
  ports are allocated per panel and reallocated across restarts, so this process is the only party that knows the answer; publishing it is what keeps the
  integration from asking a user to go read a number out of an add-on log. Neither is emitted for a panel on the standard ports, which is what hardware in
  that position advertises.
- **The dashboard shows the pair** (`:8081/9081`) next to each running panel, for adding a panel to Home Assistant by hand — that path has no discovery record
  to read and is asked for both numbers.

## 2.2.0 — a battery implies a MID, and PanelBench owns its config directory

Two defects with one symptom: a panel with a battery and no MID, and so nothing publishing islanding state. Reported against the add-on, where both were reachable
at once.

### Changed

- **A commissioned battery now defaults to grid-forming.** The gate used to claim less when nothing was declared, on the reasoning that not every battery backs the
  premises up. That reads as the careful choice and was not: the configs that declare nothing are the overwhelming majority — everything the flat simulator ever
  wrote, every clone taken before `grid_forming` existed, and two of this repository's own shipped defaults — so the cautious default withheld the MID from
  almost every real config and left a consumer with no islanding state and no error explaining why. A site whose battery is grid-following still says so with
  `grid_forming: false`, which is a statement someone makes knowingly rather than one thousands of existing configs make by omission.
- **The hybrid-PV inference is gone.** It could only ever answer "yes", which the default now covers, and its "no" for a non-hybrid inverter is what took the MID
  away from a grid-forming battery beside AC-coupled solar — an ordinary installation.
- **No battery, no island.** Islandability now requires an enabled BESS before anything else is consulted, so a panel that inherited `islandable: true` with no
  BESS stops claiming a capability it cannot have. This also keeps the widened default away from the *panel* `islandable` metadata key, which unlike the MID gate
  has no battery check of its own.

### Fixed

- **`default_MAIN_16` and `default_MAIN_32` published no MID.** Both enable a battery, and a fresh install of either — nothing stale, nothing upgraded — showed a
  battery with no islanding state. Every unit test around the gate passed while this was true, because each one builds its own profile; the invariant is now
  asserted against the configs actually on disk. Both also gain the `mid_*` identity keys, so the device-registry row a consumer builds is not a blank model with
  no firmware.
- **PanelBench no longer shares a config directory with the flat simulator.** Both add-ons used `/config/span_simulator`: the slug changed in the rename and the
  path did not. Because seeding only ever creates a file that is missing, whichever add-on ran first decided what the other one loaded, permanently — a host that
  had run the flat simulator handed PanelBench a flat-era `default_MAIN_40.yaml` with no `grid_forming` and no `islandable`, and the battery published no MID.
  The directory is now `/config/panelbench`.

  Nothing is migrated. A file at the old path may have been written by either add-on and nothing distinguishes them, so copying would reintroduce the defect this
  removes. Start-up says where the old configs are and what to check before copying one back, rather than leaving them to be discovered.

## 2.1.1 — the store listing and the sidebar say what this is

No wire change. Both fixes are text a user reads before anything else, and both were left over from the flat line.

### Fixed

- **The sidebar entry reads `SPAN PanelBench`, not `SPAN Simulator`.** `panel_title` is add-on metadata the Supervisor applies from the *installed* version, so
  unlike the store README this could not reach an existing install without a version bump — which is the whole reason this is a release rather than a text edit.
- **The App Store listing named a compatibility floor that cannot read `2.x`.** It claimed "SpanPanel/span integration version v2.0.4 or later", which shipped
  in April against the flat schema. The real floor is integration `v2.1.0`. Every earlier release reads the flat schema only and cannot read anything this
  publishes, so the old line pointed users at a version guaranteed not to work. Stated as the integration version a user actually installs and nothing else:
  the listing names one number to compare against, because collapsing two packages into one figure is how the old line went wrong in the first place. It also
  states the firmware this emulates, `r202633+`, which it had never mentioned despite that being the premise.

## 2.1.0 — the MID follows the battery, and the two generation signals agree

The first release the Supervisor will actually offer. `build-addon.yaml` has pushed an image for every `main` commit since the split, so `2.0.0` and `latest`
have been live on `ghcr.io/spanpanel/panelbench/{arch}` the whole time — but the Supervisor decides "update available" by comparing `config.yaml`'s `version`
against the installed one, and that string sat at `2.0.0` for fifty commits. Anyone already on `2.0.0` was never offered any of them. Bumping the version is
what publishes them.

The minor bump also marks a change in what this produces on the wire. A consumer that captured a `2.0.0` tree and compares it against a `2.1.0` one will see
differences, none of them a bug fix to the consumer's side.

### Fixed

- **`dataModelVersion` is published on the REST schema endpoint.** It was absent, so a consumer dispatching on the REST signal — which is what the migration
  guide's "Schema-generation detection" says to do, because the parser must exist before the first SUBSCRIBE — selected the *flat* parser for a parent/child
  tree. It did not fail: it read every value against the wrong vocabulary and reported a clean connection. The MQTT half of the same signal was already
  published, so the two transports disagreed and nothing looked.
- **The MID follows the grid-forming BESS, not the PV inverter.** `devices/bess.md` makes MID presence the classifier for premises-segment backup and requires
  a grid-forming BESS publisher to include a MID child. The gate read the PV inverter's type instead. The two agree for a hybrid-inverter site, which is why a
  MID appeared at all, and diverge for the cases the spec cares most about: a grid-forming battery with AC-coupled PV, and a grid-forming battery with no PV —
  the canonical residential backup product, which published no MID at all. `bess.grid_forming` states it outright now; `panel_config.islandable` and a hybrid
  PV inverter still work as legacy signals, so configs written before the key keep their MID.
- **EVSE identity is the Drive's serial, not its position.** Charger node ids were positional slots, so a consumer keyed on them would re-key every charger
  when one was added or removed. Serials are lowercased to satisfy the Homie topic-id rule.
- **The four `power-flows` terms sum to zero.** The emitter published three of them in the meter's reference frame rather than the enclosure's, so a consumer
  adding them up got a residual of twice `site` instead of nothing. `site` is the fourth flow under one reference direction, not a total of the other three.
  This repository's captures are the evidence base the consumer library derives its sign convention from, so a producer that contradicted it was the more
  expensive of the two things to leave wrong. Fixed upstream and carried by the `ebus-panel-sim` pin below; `tests/conformance/test_power_flow_balance.py`
  asserts the identity on every device that publishes the node, so it cannot regress silently.
- **Declared identity is valued rather than left empty.** Eleven properties were declared and never given a value, which reaches a user as an entity stuck on
  "unknown" rather than as an entity they notice is missing. The MID went from a device-registry row with a blank model and no firmware to 8 of 8 documented
  properties, PV to 4 of 5, and every shipped circuit template now carries the device identity `r202633` documents. `status/wifi-ssid` is valued too, with a
  fallback for a config that names no SSID. Six gaps remain and none are ours to invent: the four lugs `connection/*` and circuit `connection/count` are the
  upstream issue filed as DES #30, and the PV serial is deliberately still unvalued because setting it would move the PV device id and stop `simupgrade`
  rehearsing an upgrade.
- **`connection/count` is no longer declared at all**, so it can no longer be declared-but-unvalued.

### Changed

- **Circuits default to `upstream-of-lugs`.** The old default put every circuit past the feedthrough, which made the feedthrough sum equal the whole panel and
  the two lugs devices publish byte-identical meters — indistinguishable, so a mapping that swapped them read the same either way.
- **Spec catalogs re-vendored, and the pins moved with them.** `spec/catalogs` re-synced byte-for-byte from the specification, framework `0.7` -> `0.9` and
  `power-flows` `0.2` -> `0.3`. The `power-flows` correction qualifies its own negation table — the `grid` row holds only where the service lugs are the
  utility connection point, which an upstream DER or an enclosure chain breaks — and this repository publishes nothing that moves under it, so the catalog's
  byte diff is the version string. The golden report gains 68 omissions, all `meter/shared-with-device-ids` and `switch/shared-with-device-ids`, with zero
  change to divergences, extensions, matches or violations: newer catalogs describing more surface, not the emitter regressing against any of it.
- **Dependencies are plain pins on published wheels again.** `ebus-panel-sim` `0.5.1` -> `0.6.1` and `ebus-sdk` `0.20.1` -> `0.22.0`, and the
  `[tool.uv.sources]` fork override that carried the sign fixes while they were unmerged is gone. That override had a hole worth recording: the add-on
  `Dockerfile` runs `pip install .`, and `[tool.uv.sources]` is a uv table `pip` does not read, so local runs and CI got the fork while the shipped image got
  the unfixed PyPI wheel. `0.22.0` is the newest SDK `ebus-panel-sim` 0.6.1 allows, its declared range being `>=0.20.1,<0.23`; nothing here reaches what
  `0.23.x` adds, which is a producer-side `DeviceTreeBuilder` API the emitter has not adopted.

## 2.0.0 — parent/child eBus schema, in its own repository

Published as an image but never as a version: `build-addon.yaml` pushed `2.0.0` and `latest` from the first `main` commit onward, and the version string then
stayed put, so the Supervisor had nothing to compare against and offered no update. See `2.1.0`. The version is `2.0.0` because the wire format this produces
is incompatible with everything the `1.x` line published, not because a release was imminent — no SPAN firmware publishes the v1.0 tree yet.

### Changed

- **Split from `SpanPanel/simulator` into `SpanPanel/panelbench`.** The two publish incompatible schemas and a publisher cannot hot-load a wire format the way
  `span-panel-api` hot-loads a parser, so the parent/child work could never merge back — a branch that can never merge is a fork wearing a branch's clothes. The
  flat simulator keeps its name, its repository URL and its add-on slug, so no installed add-on is disturbed. History was carried across intact rather than
  squashed.
- **Renamed throughout to `panelbench`**: the Python package, the CLI entry point, the add-on directory and slug, the container image path, and the TLS and
  discovery hostname. The add-on directory has to match the slug, and the Supervisor derives a container's Docker DNS name from it, so those move together or
  not at all.

- **The emitter publishes the parent/child (v1.0) Homie data model** instead of the flat
  schema. Every circuit, BESS, PV, EVSE, lugs and MID is now its own Homie device with its
  own `$description` and `$state`, rather than a namespaced node hanging off the panel.
  Topics move from `ebus/5/<panel>/<circuit>/<prop>` to
  `ebus/5/<circuit>/<capability>/<prop>`.
- **The package is `ebus_emitter`**, renamed from `flat_emitter` — the old name described
  the schema rather than the component, so it would have needed renaming again at the next
  schema change.
- **`dominant-power-source` is split**, following the upstream migration. Its identity half
  is the MID's read-only `grid/grid-forming-entity`; its settable half is the panel's
  `shed/asserted-islanding-state` (`NONE` / `ON_GRID` / `OFF_GRID`). The assertion now
  drives load-shed treatment — auto-shed runs when the effective islanding state is not
  `ON_GRID` — where previously the override reached only a published value and influenced
  no decision. It overrides shed treatment only, never physics.
- **`<circuit>/info/name` is read-only.** There is no circuit rename over eBus in v1.0, so
  the settable-name handler is gone. The complete settable set is four topics: circuit
  `switch/relay`, circuit `load-shed/priority`, panel `shed/asserted-islanding-state`, and
  EVSE `config/user-max-charge-current`.
- **`ebus-sdk` moves to 0.17.0**, which carries two fixes this depends on: a transport-free
  root that accepts children, and a missing client logged at `debug` rather than `warning`
  when the tree is transport-free by design.

### Added

- **`.ebus-spec.json`**, the eBus specification provenance lockfile, declaring the spec
  commit this repository was reconciled against and the artifacts it implements. CI verifies
  every vendored capability catalog is byte-identical to the specification at that commit,
  so the base copies are read-only by enforcement rather than convention, and reports drift
  against the current spec.
- **The panel publishes `info/data-model-version`**, overridable from manifest metadata so a
  fixture can advertise a stale or future version and exercise a consumer's drift detection
  — something real firmware cannot be asked to do on demand.

### Fixed

- **Voltage properties published with no unit.** `_to_sdk_unit` resolved through a hand-written
  table that mapped `"V"` to a non-existent SDK enum member; it now resolves by enum value.
- **`set_property_value` assigned to `Property.coerced_value`**, which is a zero-arg getter —
  the assignment would have replaced the method rather than setting a value. Unreachable in
  practice, since the SDK has always exposed `set_value`.

## 1.0.14 — 2026-07-31 — vendor the flat emitter

### Fixed

- **The HA add-on image could never start.** `ebus_emitter` is a hard, unconditional import
  (`app.py` → `emitter_adapter/runtime.py`), but the Dockerfile installs only
  `pip install --no-cache-dir .`, and the package was not a declared dependency — it is not
  on PyPI and was installed editable from `EBUS_EMITTER_PATH` by `scripts/dev-setup.sh`.
  The image therefore built successfully and failed at container start with
  `ModuleNotFoundError: No module named 'ebus_emitter'`. The same applied to anyone who
  cloned this repo and ran `uv sync` without `dev-setup.sh`. Vendoring removes the external
  dependency entirely, so both paths now work.

### Changed

- **The flat emitter is vendored at `src/panelbench/flat_emitter`**, copied from
  `ebus-emitter` 0.2.1 (commit `5b84de8`) — MIT, same copyright holders. The upstream repo
  has permanently diverged onto the parent/child (v1.0) Homie data model while this
  simulator continues to publish the flat schema, so the dependency delivered no upstream
  changes while costing path configuration, stale editable metadata, and an unsolvable
  distribution problem for the add-on. See the package docstring for full provenance.

  It also closes a correctness hazard: `clone.py` seeds energy accumulators against what
  this code publishes, and while the two lived in separate repos each side could look
  locally correct while jointly inverting circuit energy — which is exactly what happened.
  Both ends now sit in one repo under one test run.

- **The emitter's test suite came with it** (`tests/ebus_emitter/`, 154 tests), including
  the circuit energy reference-frame regression tests. Total suite is now 395 tests.

- **`scripts/dev-setup.sh` is now a thin `uv sync` wrapper** and `.env.example` no longer
  defines `EBUS_EMITTER_PATH`; every dependency resolves from PyPI.

- **`ebus-sdk` is pinned to `==0.1.5`** rather than the range upstream declared, so that
  vendoring is behaviour-neutral: 0.1.5 is what the emitter's lockfile resolved and what
  this code was tested against. Letting it float within `<0.2` resolves 0.1.10, which drops
  the module-level `setLevel(INFO)` on the `homie` logger that `tests/test_main_logging.py`
  guards. Raising it is a deliberate follow-up, not a side effect of moving code.

- **`[tool.ruff.lint]` now declares `ignore = ["TC001", "TC002", "TC003"]`.** The existing
  comment already described this ignore, but the key was never present — none of the
  simulator's own modules happened to trigger the rules, so the omission was invisible.
  The vendored code was authored under an identical select list plus this ignore.

- **`ChargeMode` is exported from the vendored package** and used to annotate the
  `charge_mode` derivation in `engine.py` and `emitter_adapter/runtime.py`. Both sites
  already produced only valid values; mypy could not see it while `ebus_emitter` was an
  `ignore_missing_imports` module and `BESSConfig` was therefore `Any`.

> **Version note.** This work merged as 1.0.13, the same version the circuit-energy fix
> below had already published. The add-on image tag is derived from `config.yaml`, so the
> second merge overwrote the first's image without changing the version — leaving anyone
> who had already pulled 1.0.13 on the earlier build with no update signal. Re-cut as
> 1.0.14 so Supervisor sees a change.

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
