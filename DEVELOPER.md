# Developer Guide

## Prerequisites

- Python 3.14+ (`pyproject.toml` sets `requires-python = ">=3.14"`)
- [uv](https://docs.astral.sh/uv/) (`brew install uv` on macOS)

## Setup

```bash
# Clone and enter the repo
git clone https://github.com/SpanPanel/panelbench.git
cd simulator

# Create venv and install all dependencies (runtime + dev)
# uv reads pyproject.toml and uv.lock, creates .venv/ automatically
uv sync --group dev

# Install pre-commit hooks
uv run pre-commit install
```

That's it. The `.venv/` directory is created in the project root and `uv.lock` pins exact versions for reproducible installs.

## Common Commands

```bash
# Run the simulator
uv run panelbench

# Run tests
uv run pytest

# Lint + format
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/

# Type check
uv run mypy --strict src/panelbench/

# Add a new dependency
uv add <package>          # runtime
uv add --group dev <pkg>  # dev only
```

## Pre-commit Hooks

Every commit is validated by:

| Hook                        | What it checks                                              |
| --------------------------- | ----------------------------------------------------------- |
| **ruff**                    | Lint rules (E, F, W, I, UP, B, SIM, TCH, RUF) with auto-fix |
| **ruff-format**             | Consistent formatting                                       |
| **mypy --strict**           | Full strict type checking across all source files           |
| **trailing-whitespace**     | No trailing whitespace                                      |
| **end-of-file-fixer**       | Files end with a newline                                    |
| **check-yaml**              | Valid YAML syntax                                           |
| **check-added-large-files** | Prevents accidental large file commits                      |

## Spec Conformance

### What this simulator is, and why conformance is the whole point

This simulator pretends to be a SPAN panel on an MQTT broker. It publishes a device tree in the Homie 5 format, following the eBus specification — an open,
vendor-neutral vocabulary for electrical devices maintained at [`electrification-bus/specification`](https://github.com/electrification-bus/specification).

People build software against this simulator instead of against real hardware. If we publish something subtly wrong, that error does not stay ours: a consumer
is written to cope with our mistake, and then breaks against a real panel that does it correctly. **A simulator that is wrong in a way nobody notices is worse
than no simulator.** So being provably faithful is not a nicety here — it is the product.

### Where the vocabulary comes from — and what we vendor

The specification defines **capabilities** (`meter`, `soc`, `breaker`, …), each with a **catalog**: a JSON file listing the properties that capability may
publish and, for each, its datatype, unit and format.

**We vendor that vocabulary — we do not depend on a package for it.** There is no published package to depend on: the specification is a git repository of
versioned documents and JSON, and it is not on PyPI. Copying is not a workaround here, it is the model upstream designed for. `conventions/spec-provenance.md`
calls the lockfile below "the eBus analogue of a dependency lockfile (`package-lock.json`, `Cargo.lock`)" and expects downstreams to copy specific artifacts at
specific versions rather than depend on "the spec" as a whole.

What is vendored, and from where:

| In the repo                                   | Vendored from                       | Pinned?                            | Provenance recorded in     |
| --------------------------------------------- | ----------------------------------- | ---------------------------------- | -------------------------- |
| `wire/catalogs/*.json` (15 files)             | the specification repo              | yes, byte-compared in CI           | `.ebus-spec.json`          |
| `wire/profiles/*.json`, `wire/mapping/*.yaml` | panel-sim, not the spec directly    | no, they are not spec artifacts    | `.ebus-spec.json` notes    |
| `ebus_emitter/` itself                        | the upstream `ebus-emitter` package | at a commit, but no longer tracked | `ebus_emitter/__init__.py` |

Each of those carries its own provenance note; read it before changing anything under that directory, because the rules differ (the catalogs are byte-compared
and must not be edited, while the emitter is a permanent fork where you fix bugs in place rather than porting from upstream).

The one thing we do **not** vendor is `ebus-sdk`, which is a normal pinned PyPI dependency (`pyproject.toml`). It supplies the Homie mechanics — `Device`,
`Node`, `Property` — while the vendored catalogs supply the vocabulary. Code from PyPI, data vendored.

`.ebus-spec.json` at the repo root is the lockfile: it records the specification commit the catalogs were copied from and the version of each artifact we
implement. `scripts/check-spec-provenance.py` verifies the copies are byte-identical at that commit and separately reports how far behind current the pins have
fallen.

**Never hand-edit a file under `catalogs/`.** They are byte-compared against the specification in CI, and an edit fails the build. That check is what makes the
copies trustworthy, so keeping them pristine matters more than the convenience of a local tweak. Any SPAN-specific difference belongs in the
`wire/profiles/span/` overlay, where it is visible as a deliberate divergence rather than hidden inside a mirror.

#### The part of the vocabulary that does not travel

Vendoring copies files, and some of what a publisher must know is not _in_ the files. One rule matters enough to name here, because it is the reason the
conformance checker exists.

The specification requires a publisher to substitute a real unit wherever a catalog carries an abstract token like `"unit": "energy"`. That requirement lives in
the specification's prose and in a constant inside the specification repo's own tooling — neither of which a downstream copies. The catalogs we vendor contain
the token but carry no machine-readable marker saying it is special: `"unit": "energy"` and `"unit": "V"` are the same shape.

So we hand-carry it, in exactly one place: `ABSTRACT_UNITS` in `src/panelbench/conformance/catalogs.py`. It is a single constant with a comment citing
the upstream source, both mechanisms that enforce the rule read from it, and a test fails on a re-vendor that introduces a unit it does not account for. If
upstream ever ships this as data, that constant should be deleted in favour of it.

Worth knowing when reading the code: this is deliberate, minimal duplication of upstream knowledge, not an oversight. It is the cost of vendoring, and it is
bounded to one line and one test rather than spread through the emitter.

### The two checks, and why one is not enough

| Check                              | Proves                                       |
| ---------------------------------- | -------------------------------------------- |
| `scripts/check-spec-provenance.py` | specification bytes → our vendored catalogs  |
| `scripts/check-conformance.py`     | vendored catalogs → what we actually publish |

Provenance proves we copied the right bytes. It cannot prove we _understood_ them, and that gap is real rather than theoretical.

A worked example, which is why this tooling exists at all. Four catalog properties carry `"unit": "energy"`. That is not a unit — it is a placeholder naming a
_dimension_, because a battery reports energy in kWh while a water heater reports it in Wh, and the catalog cannot pick one. The specification requires each
publisher to substitute a real unit. Our catalog copies of those four properties were byte-perfect and our lockfile was truthful, yet the code reading them
would have published the property **with no unit at all** — because the SDK's unit type cannot represent the placeholder, and our conversion quietly drops
anything it cannot represent. No exception, no warning, just an energy reading a consumer cannot interpret.

No amount of copy-checking finds that. Only looking at what actually went out on the wire does.

### How the conformance checker works

Every device publishes one retained MQTT message called `$description` — a JSON document listing its nodes and properties with their datatype, unit, format and
settability. That document is the device's own statement of what it publishes, so it is all the checker needs.

```text
$description documents  →  parse into a typed tree  →  compare against catalogs  →  report
```

The comparison is deliberately **not** a strict match, because the specification is deliberately permissive. A publisher may omit any property it does not
support, publish properties the spec has never heard of, and widen a datatype. The specification states one standing obligation: _be self-describing_ — declare
accurately what you publish. A checker that failed on every difference from the catalog would fail correct publishers and quickly stop being run.

So every published property is sorted into one of five buckets:

| Bucket         | Meaning                                                            | Fails the build? |
| -------------- | ------------------------------------------------------------------ | ---------------- |
| **match**      | in the catalog, and shaped the way the catalog describes           | no               |
| **divergence** | in the catalog, but we publish it differently — legal              | no               |
| **extension**  | not in any catalog; ours alone — legal                             | no               |
| **omission**   | in the catalog, we do not publish it — legal, publishing is opt-in | no               |
| **violation**  | breaks Homie 5, or one of eBus's few hard requirements             | **yes**          |

Only four things count as violations: an `enum` or `color` property with no format (Homie requires it), a unit placeholder reaching the wire, a placeholder-unit
property published with no unit, and a device named as a child that never published a description.

The other four buckets are the actually useful output. Together they are a **conformance profile** — a precise statement of what this simulator emits, which
parts are shared eBus vocabulary and which are SPAN's own. That is the contract someone writing a consumer codes against, and because it is derived from the
wire it cannot drift the way hand-written integration notes always do.

Current profile: 557 match, 4 divergence, 82 extension, 1486 omission, 0 violations. All four divergences are deliberate SPAN choices, such as `shed/policy`
being read-only because a real SPAN panel does not accept policy writes.

### Running it

```bash
# Verify the vendored catalogs against the specification at synced_commit,
# and report drift against the current spec (informational)
uv run scripts/check-spec-provenance.py

# Check a captured tree. Exit 0 means no violations.
uv run scripts/check-conformance.py --capture tests/conformance/fixtures/golden_tree.json

# Add --verbose to list omissions, or --json for the machine-readable profile
```

`tests/conformance/` runs both a captured tree and hand-built cases on every `pytest` run, so you do not need a broker to get the check. Recapture only when the
published surface changes on purpose:

```bash
# Start the simulator first, then:
mosquitto_sub -h localhost -p 18883 --cafile .local/certs/ca.crt \
  -u span -P sim-password -t 'ebus/5/+/$description' -v -W 5 \
  | uv run scripts/check-conformance.py --from-stdin \
  > tests/conformance/fixtures/golden_tree.json
uv run scripts/check-conformance.py \
  --capture tests/conformance/fixtures/golden_tree.json --json \
  > tests/conformance/fixtures/golden_report.json
```

`test_golden_report_is_unchanged` compares the full profile, so it fails on _any_ change to what we publish — including a perfectly legal one. That is intended:
a change to our published surface should always be a reviewed edit to the expected profile, never something that slips through because no rule happened to cover
it.

### The wire capture, and why it is a second fixture

`golden_tree.json` holds `$description` documents, because conformance is a question about what a device *declares*. That is not enough to exercise a **consumer**:
a parser fed only descriptions can be asked whether it understands the shape of a panel, never whether it builds the right snapshot from one — which is the part
that reaches a user. `golden_wire.json` holds the whole retained surface instead: descriptions, `$state`, and every property value, keyed the way a consumer
receives them.

```bash
# Recapture. No broker needed — the emitter is assembled with the MQTT client substituted.
uv run scripts/capture-wire.py
```

It goes through `start_clone`, the same assembly a real panel uses, with `publisher=` supplying a recorder in place of the aiomqtt client. Reassembling the
emitter inside the script would make it a second copy of that wiring, free to drift, and a capture taken through different wiring than a panel uses proves less
than it appears to.

**Its values are not reproducible and nothing asserts them.** The config carries `noise_factor` and the clock advances, so power and current differ every run.
`test_wire_capture.py` compares *shape* — which devices, which topics — so a property added, removed or renamed fails it while a different wattage does not. That
is the opposite policy to `golden_report.json`, deliberately: a conformance profile must not drift silently, and a fixture full of noisy floats cannot be held to
byte equality without producing failures nobody can act on.

`span-panel-api-schema-1` vendors this capture and drives its parser end to end from it, which is the point of publishing it as an artifact rather than keeping it
internal.

### If you are changing the emitter

Read [`docs/spec-conformance-design.md`](docs/spec-conformance-design.md) before adding a capability to a profile or changing how properties reach the wire. Two
things will stop you early if you get the unit wrong: `profile_loader` refuses to load a profile that inherits a unit placeholder without substituting a real
unit, and the conformance checker rejects one that reaches the wire.

The `conformance` package deliberately imports nothing from the rest of this simulator and nothing MQTT-related — a test enforces it. That keeps it a general
eBus publisher checker rather than a SPAN-specific one, and it is why the rules describe _any_ publisher's output rather than just ours.

## Running Locally

There are three ways to run the simulator locally, depending on what you're testing:

### 1. Native (recommended for development)

Runs directly on the host with full mDNS visibility. Best for iterating on simulator code and testing integration discovery.

```bash
./scripts/run-local.sh
```

To connect to a Home Assistant instance for entity discovery and recorder statistics (needed for profile import, cost modeling, and the data acquisition layer),
pass your HA credentials:

```bash
./scripts/run-local.sh --ha-url http://192.168.1.10:8123 --ha-token YOUR_LONG_LIVED_TOKEN
```

Or via environment variables:

```bash
export HA_URL=http://192.168.1.10:8123
export HA_TOKEN=YOUR_LONG_LIVED_TOKEN
./scripts/run-local.sh
```

When running as an HA add-on, the Supervisor injects `SUPERVISOR_TOKEN` automatically and these are not needed. See `ha_api/client.py` for the dual-mode
detection logic.

### 2. Docker container

Builds and runs the same image that CI pushes to GHCR. Useful for verifying the container works before pushing. mDNS auto-discovery won't work on macOS (VM
networking), but the integration can connect by manual configuration. On Linux, Docker with host networking works.

```bash
# Build (use aarch64 base on Apple Silicon, amd64 on Intel/Linux)
docker build -f panelbench/Dockerfile \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.14-alpine3.23 \
  -t panelbench:local .

# Run
mkdir -p .local/addon-test
cat > .local/addon-test/options.json <<'EOF'
{
  "config_file": "span_simulator/default_config.yaml",
  "tick_interval": 1.0,
  "log_level": "INFO",
  "advertise_address": "",
  "dashboard_enabled": true
}
EOF

docker run --rm \
  -p 18883:18883 -p 8081:8081 -p 18080:18080 \
  -v $(pwd)/configs:/config/span_simulator \
  -v $(pwd)/.local/addon-test:/data \
  panelbench:local
```

### 3. HA add-on

Requires a Home Assistant instance running HA OS or a supervised install. Add the repo URL as a custom repository and install from the Add-on Store. This is the
only way to test the full add-on lifecycle.

## Multi-Panel Limitations

The simulator can load multiple configs, but each panel shares the same host IP and HTTP server. Since a real SPAN panel has its own IP, the integration's
discovery flow deduplicates panels that resolve to the same address.

For true multi-panel simulation, assign separate IPs to the host:

```bash
# macOS — add an alias IP
sudo ifconfig en0 alias 192.168.7.27 255.255.255.0

# Run one simulator per IP
ADVERTISE_ADDRESS=192.168.7.26 CONFIG_DIR=./configs/panel1 ./scripts/run-local.sh
ADVERTISE_ADDRESS=192.168.7.27 CONFIG_DIR=./configs/panel2 ./scripts/run-local.sh
```

---

## Full Config Schema

```yaml
panel_config:
  serial_number: str # Unique panel serial (e.g., "SPAN-SIM-001")
  total_tabs: int # Breaker tab count (8, 32, 64)
  main_size: int # Main breaker amps (100, 150, 200)
  latitude: float # Degrees north (default: 37.7)
  longitude: float # Degrees east (default: -122.4)
  time_zone: str # IANA timezone (default: resolved from lat/lon)
  soc_shed_threshold: float # SOC % for SOC_THRESHOLD shedding (default: 20)

circuit_templates: # Reusable template definitions
  template_name:
    energy_profile:
      mode: str # "consumer" | "producer" | "bidirectional"
      power_range: [min, max] # Watts (negative = production)
      typical_power: float # Base power in watts
      power_variation: float # Fraction (0.1 = +/-10%)
      efficiency: float # 0.0-1.0 (optional, PV/battery)
      nameplate_capacity_w: float # PV nameplate rating in watts
      initial_consumed_energy_wh: float # Seed consumed energy (from clone)
      initial_produced_energy_wh: float # Seed produced energy (from clone)
    relay_behavior: str # "controllable" | "non_controllable"
    priority: str # "NEVER" | "SOC_THRESHOLD" | "OFF_GRID"
    device_type: str # "circuit" | "evse" | "pv" (default: "circuit")
    breaker_rating: int # Amps (derived from power_range if not set)

    # Optional behavioral modules
    cycling_pattern:
      on_duration: int # Seconds on (explicit mode)
      off_duration: int # Seconds off (explicit mode)
      duty_cycle: float # 0.0-1.0 — fraction of cycle spent on (from HA stats)
      period: int # Total cycle length in seconds (default: 2700)
    hvac_type: str # "central_ac" | "heat_pump" | "heat_pump_aux"
    monthly_factors: # Month (1-12) -> multiplier (1.0 = peak month)
      1: 0.6 # Takes precedence over hvac_type seasonal model
      7: 1.0

    time_of_day_profile:
      enabled: bool
      peak_hours: [int] # Hours 0-23
      hour_factors: # Per-hour multiplier (0.0-1.0)
        0: 1.0
        6: 0.0
        18: 1.0
      hourly_multipliers: # Alternate per-hour override
        6: 0.1
        12: 1.0

    smart_behavior:
      responds_to_grid: bool
      max_power_reduction: float # 0.0-1.0

    battery_behavior:
      enabled: bool
      charge_mode: str # "custom" | "solar-gen" | "solar-excess"
      nameplate_capacity_kwh: float # Total battery capacity (default: 13.5)
      backup_reserve_pct: float # Normal discharge floor % (default: 20)
      charge_efficiency: float # 0.0-1.0 (default: 0.95)
      discharge_efficiency: float # 0.0-1.0 (default: 0.95)
      charge_power: float
      discharge_power: float
      idle_power: float
      max_charge_power: float # Used by solar-gen/solar-excess modes
      max_discharge_power: float
      charge_hours: [int]
      discharge_hours: [int]
      idle_hours: [int]

circuits:
  - id: str # Unique identifier
    name: str # Human-readable name
    template: str # References a circuit_templates key
    tabs: [int] # Tab positions ([1] = 120V, [1, 3] = 240V)
    breaker_rating: int # Per-circuit override (optional)
    overrides: # Override any template field
      typical_power: 500.0

unmapped_tabs: [int] # Tab numbers with no circuit assigned

simulation_params:
  update_interval: int # Seconds between snapshots (default: 5)
  time_acceleration: float # 1.0 = real-time, 2.0 = double speed
  noise_factor: float # Random noise fraction (0.02 = +/-2%)
  enable_realistic_behaviors: bool

# Clone provenance (written by the clone pipeline)
panel_source:
  origin_serial: str # Real panel's serial (immutable)
  host: str # IP or hostname of source panel
  passphrase: str | null # Proximity code (null for door-bypass)
  last_synced: str # ISO 8601 timestamp
```

### Shed Priority

Circuit shed priority controls backup behavior when the grid disconnects, matching the Homie v2 schema (`shed-priority` property):

| Priority        | Behavior                                                |
| --------------- | ------------------------------------------------------- |
| `NEVER`         | Never shed — stays on as long as battery has power      |
| `OFF_GRID`      | Shed immediately when dominant power source leaves GRID |
| `SOC_THRESHOLD` | Shed when battery SOC drops below `soc_shed_threshold`  |

The `soc_shed_threshold` in `panel_config` (default 20%) sets the SOC percentage that triggers shedding for `SOC_THRESHOLD` circuits.

User relay overrides (from dashboard or MQTT) take precedence over shedding — if a user closes a shed relay, shedding will not reopen it.

---

## HTTP API

### Bootstrap Endpoints (eBus v2)

These endpoints match the real SPAN panel's API exactly.

| Method | Path                     | Description                                        |
| ------ | ------------------------ | -------------------------------------------------- |
| `GET`  | `/api/v2/status`         | Panel identity (`serialNumber`, `firmwareVersion`) |
| `POST` | `/api/v2/auth/register`  | Returns MQTT credentials and broker details        |
| `GET`  | `/api/v2/certificate/ca` | Self-signed CA certificate (PEM)                   |
| `GET`  | `/api/v2/homie/schema`   | Homie v5 property schema                           |

Query `/api/v2/status?serial=XXX` to target a specific panel when multiple are loaded.

### Admin Endpoints

| Method | Path            | Description                                   |
| ------ | --------------- | --------------------------------------------- |
| `POST` | `/admin/reload` | Hot-reload configs (add/remove/update panels) |
| `GET`  | `/admin/panels` | List all running panels                       |

```bash
# Reload after editing configs
curl -X POST http://192.168.7.26:8081/admin/reload

# List panels
curl http://192.168.7.26:8081/admin/panels
```

---

## MQTT Topics

The simulator publishes Homie v5 messages on the eBus topic namespace:

```text
ebus/5/{serial}/{node}/{property}
```

### Nodes

| Node              | Description                                                            |
| ----------------- | ---------------------------------------------------------------------- |
| `core`            | Panel state: door, relay, voltages, grid status, dominant power source |
| `upstream-lugs`   | Grid-facing: power, currents, energy                                   |
| `downstream-lugs` | Load-facing: feedthrough power, currents                               |
| `{circuit-uuid}`  | Per-circuit: relay, power, energy, shed-priority                       |
| `bess-0`          | Battery: SOC, grid-state, capacity                                     |
| `pv-0`            | Solar inverter: nameplate capacity                                     |
| `evse-0`          | EV charger: status, lock state, advertised current                     |
| `power-flows`     | Aggregated: PV, battery, grid, site power                              |

### Settable Properties

Control circuits by publishing to `/set` topics:

```bash
# Open a circuit relay
mosquitto_pub -t "ebus/5/SPAN-TEST-001/{uuid}/relay/set" -m "OPEN"

# Change shed priority
mosquitto_pub -t "ebus/5/SPAN-TEST-001/{uuid}/shed-priority/set" -m "OFF_GRID"

# Change dominant power source (triggers load shedding)
mosquitto_pub -t "ebus/5/SPAN-TEST-001/core/dominant-power-source/set" -m "BATTERY"
```

Relay and priority changes made via MQTT are reflected in the dashboard in real time.

---

## Panel Cloning

The simulator can clone a real SPAN panel's configuration over its eBus. Cloning is initiated from the dashboard's Clone panel form. The simulator authenticates
with the target panel, scrapes its MQTT topics, translates the eBus description into a simulator YAML config, and hot-reloads.

### What gets cloned

- Panel identity (`sim-{serial}-clone`), main breaker rating, panel size
- All circuits: name, tab position, breaker rating, relay behavior, priority
- Energy profile mode inferred from device feeds (PV -> producer, BESS -> bidirectional, EVSE -> bidirectional)
- Energy accumulators seeded from the panel's imported/exported energy values
- Battery behavior with sensible schedule defaults
- PV nameplate capacity and production profile
- EVSE night-charging time-of-day profile
- Source panel credentials stored in `panel_source` for on-demand refresh

### Usage profile import

When connected to Home Assistant, the simulator can derive per-circuit usage profiles from the HA recorder's long-term statistics. This replaces the clone's
point-in-time power readings with patterns grounded in actual household behavior:

- **typical_power** -- mean of hourly means over 30 days
- **power_variation** -- coefficient of variation (stddev/mean)
- **hour_factors** -- 24-hour shape normalized to peak = 1.0
- **duty_cycle** -- mean/max ratio (skipped if >= 0.8)
- **monthly_factors** -- 12-month seasonality (requires 3+ months of data)

The cloned config is a faithful starting point. Behavioral tuning (profiles, cycling patterns, smart behavior) can be adjusted via the dashboard after cloning.

---

## Simulation Engine Internals

### Power Calculation (per tick)

1. Apply relay and priority overrides (immediate effect)
2. Check relay state (open = 0W)
3. Apply base power from `typical_power` with `power_variation` randomness
4. Producers: geographic sine-curve solar model with weather degradation
5. Consumers: modulate by time-of-day profile / hour factors (if configured)
6. HVAC seasonal modulation (latitude-aware temperature model scales power by season)
7. Apply cycling pattern on/off state (if configured)
8. Apply battery charge/discharge schedule or solar charge mode (if configured)
9. Apply smart grid response (if configured)
10. Add noise (`noise_factor`)
11. Apply load shedding overlays (if grid offline with battery)

### Site Power vs Grid Power

The engine separates two distinct power measurements:

- **Site power** = net demand at the panel lugs (loads - solar), independent of any upstream BESS. This is what the SPAN panel's feedthrough CTs measure.
- **Grid power** = what the utility meter sees (loads - solar +/- battery). When a BESS is present upstream, battery discharge offsets grid import and battery
  charge increases it.

See [SPAN API Client Docs](https://github.com/spanio/SPAN-API-Client-Docs) for the panel's physical topology.

### Solar

PV circuits use a geographic sine-based model:

- Sunrise/sunset computed from latitude, longitude, and date
- Solar elevation angle determines instantaneous production factor
- Daily weather degradation from Open-Meteo historical cloud cover data
- Falls back to deterministic seed-based weather when no API data available

### HVAC Seasonal Modulation

Circuits with `hvac_type` set automatically adjust power draw by season using a latitude-aware sinusoidal temperature model:

| HVAC Type       | Summer                  | Winter                            | Why                          |
| --------------- | ----------------------- | --------------------------------- | ---------------------------- |
| `central_ac`    | Full compressor (~100%) | Blower fan only (~15%)            | Gas furnace handles heating  |
| `heat_pump`     | Full compressor (~100%) | COP reduces draw (~45%)           | Heat pump efficiency in cold |
| `heat_pump_aux` | Full compressor (~100%) | Aux strips exceed cooling (~140%) | Resistive backup below ~35F  |

### Battery (BSEE)

The Battery Storage Energy Equipment tracks real state-of-energy by integrating power over time:

- **Charging**: `SOE += power * dt * charge_efficiency`
- **Discharging**: `SOE -= power * dt / discharge_efficiency`
- **Backup reserve**: Normal discharge stops at `backup_reserve_pct` (default 20%); only grid-disconnect emergencies drain to the 5% hard floor
- **Charge modes**: Custom (hour-based schedule), Solar Generation (tracks PV curve), Solar Excess (surplus after loads)

### Energy Accumulation

Energy integrates over time in watt-hours:

```text
delta_energy = power_watts * delta_seconds / 3600
```

Consumed and produced energy are tracked separately per circuit.

### Diffing

Only changed property values are republished each tick. Unchanged values are not retransmitted.

---

## Home Assistant Add-on (App)

### Directory naming matters

The `panelbench/` directory **must** match the `slug` field in `config.yaml`. The HA Supervisor uses the directory name to identify the add-on —
renaming it will break discovery. If you need to change the slug, update both the directory name and the `slug` field together.

### Build pipeline

The GitHub Actions workflow (`.github/workflows/build-addon.yaml`) builds the Docker image from the **repo root** as the build context (not from the add-on
subdirectory). This is necessary because the Dockerfile needs access to `pyproject.toml`, `src/`, and `mosquitto/` which live at the repo root.

The HA Supervisor would normally build from the add-on subdirectory, which can't reach parent files — that's why we use the `image:` field to pull pre-built
images instead.

The workflow:

- Triggers on pushes to `main` that touch source, config, or workflow files
- Builds per-architecture images (amd64, aarch64) using the appropriate HA base image
- Pushes to `ghcr.io/SpanPanel/panelbench/{arch}:{version}`

## TLS Certificates

Certificates are generated automatically on first run and cached in the cert directory. The server certificate SAN includes:

- `panelbench` (hostname)
- `localhost`
- `127.0.0.1`
- The `ADVERTISE_ADDRESS` IP (if set)

If the host IP changes, certificates are automatically regenerated on the next startup. Delete `.local/certs/` to force regeneration.

## Directory Layout

```text
simulator/
  repository.yaml            # HA app repository metadata (current YAML format)
  repository.json            # Legacy HA add-on repository metadata (JSON compatibility file)
  configs/                   # Panel YAML configurations
  panelbench/      # HA add-on (dir name must match slug)
    config.yaml              # Add-on metadata, image ref, options schema
    build.yaml               # Per-architecture base images
    Dockerfile               # Used by CI (build context is repo root)
    run.sh                   # Container entry point
    DOCS.md                  # User-facing add-on documentation
    translations/en.yaml     # Option labels for HA UI
  .github/workflows/
    build-addon.yaml         # CI: build and push images to GHCR
  docs/images/               # Screenshots
  scripts/
    run-local.sh            # macOS native (recommended)
    entrypoint.sh           # Docker entrypoint (Linux)
  src/panelbench/
    __main__.py             # CLI and entry point
    app.py                  # Multi-panel orchestrator
    panel.py                # Single panel lifecycle
    engine.py               # Power/energy simulation
    circuit.py              # Per-circuit state and snapshot
    clock.py                # Simulation clock with acceleration
    bsee.py                 # Battery storage equipment (BESS/GFE)
    hvac.py                 # Seasonal HVAC power modulation
    solar.py                # Geographic sine-curve solar model
    weather.py              # Open-Meteo historical weather
    publisher.py            # Homie MQTT publisher (with diffing)
    bootstrap.py            # HTTP API server
    discovery.py            # mDNS advertisement
    certs.py                # TLS certificate generation
    models.py               # Snapshot dataclasses
    config_types.py         # YAML schema TypedDicts
    recorder.py             # HA recorder data source and replay
    ha_api/                 # Home Assistant API client (dual-mode)
      client.py             # REST API client (Supervisor or local)
      entity_discovery.py   # SPAN device -> circuit entity mapping
      profile_builder.py    # Recorder stats -> usage profiles
    dashboard/              # Web dashboard (port 18080)
      routes.py             # HTMX route handlers
      config_store.py       # In-memory config state
      presets.py             # Profile and schedule presets
      defaults.py           # Entity type defaults
      solar.py              # Solar curve computation
      templates/            # Jinja2 templates
      static/               # CSS, JS (htmx, Chart.js, noUiSlider)
  .local/                   # Runtime state (gitignored)
    certs/                  # Generated TLS certificates
    mosquitto/              # Mosquitto config and passwd
    pids/                   # Process ID files
```
