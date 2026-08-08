# SPAN PanelBench

A SPAN panel on an MQTT broker: a conformance-checked publisher of the **eBus v1.0 parent/child device tree**, for building and verifying consumers against.

People write software against this instead of against hardware. If it publishes something subtly wrong, the error does not stay here — a consumer is written to
cope with the mistake and then breaks against a real panel that does it correctly. **A simulator that is wrong in a way nobody notices is worse than no
simulator.** So being provably faithful is the product, not a nicety, and two checks enforce it: the vendored eBus capability catalogs are byte-compared against
the specification, and everything published on the wire is checked against those catalogs. See [DEVELOPER.md](DEVELOPER.md#spec-conformance).

Includes a web dashboard for real-time configuration, grid simulation, Home Assistant history replay, and energy "what-if" modeling.

## Status: not installable yet, and that is deliberate

**No SPAN firmware publishes the v1.0 tree.** It arrives in `r202633+`.

Cloning follows the same line: it reads a panel running `r202633+` and **does not clone earlier firmware**. A flat-schema panel is
[`SpanPanel/simulator`](https://github.com/SpanPanel/simulator)'s job, not this one — the two schemas are not convertible, so there is no fallback path here.

What this is for today is the reference producer that [`span-panel-api`](https://github.com/SpanPanel/span-panel-api) and the Home Assistant integration are
developed and verified against, ahead of hardware existing.

### Which repository you want

| | Schema | Firmware | Status |
| --- | --- | --- | --- |
| **this repo** | parent/child device tree (`data-model-version` `1.x`) | `r202633+` | pre-release; no firmware yet |
| [`SpanPanel/simulator`](https://github.com/SpanPanel/simulator) | flat single-device | `r202603`–`r202627` | released, installable today |

The two are **permanently separate**, not versions of one thing. A panel speaks one schema or the other, a publisher cannot hot-load a wire format the way
`span-panel-api` hot-loads a parser, and the flat simulator is a deliberate fork that no longer tracks upstream. Bugs are fixed in whichever repo has them.
When the fleet moves to `r202633+`, the flat simulator stops being published and this becomes the one that matters.

## Workflow

Click a simulator configuration to view it. Templates are read-only. A running simulator appears as a discovered panel in the SpanPanel integration (default
configs excluded).

1. **Examine templates** — Load and run the included configs (`default_config.yaml`, `simple_test_config.yaml`, etc.) to see how circuits, PV, battery, and EVSE
   are modeled. Pick one as a starting point for your own configuration.

2. **Clone** — The **Clone** button creates an editable copy from a template, or from a panel running `r202633+` firmware; cloning a panel preserves recorder
   history per circuit.

3. **Model** — The **Model** button on a running panel opens the what-if view; add battery, PV, or circuits and compare before/after. Edits mark equipment as
   **SYN**; click the badge to revert to **REC**.

4. **Purge** — The **Purge** button removes recorder history written by the simulated panel's sensors if you added the simulated panel to Home Assistant's
   integration.

![Dashboard overview — grid offline with load shedding, live power chart, entity list with relay status](docs/images/dashboard1.png)

![PV editor — solar production curve with geographic modeling and historical weather degradation](docs/images/dashboard2.png)

![Battery editor — BESS charge and discharge profile](docs/images/dashboard_battery.png)

![Modeling view — Before/After energy comparison with BESS, dual charts with range zoom and circuit overlays](docs/images/modeling.png)

## Home Assistant App — not yet published

The add-on is built and kept working, but **no image is published for this repository yet**, because there is no firmware for it to stand in for. Adding the
repository URL to Home Assistant today will not find an installable app. Run it standalone instead — see Quick Start below.

The steps below are what will apply once `r202633+` firmware ships and this is released. Until then, for a panel you can actually install against, use
[`SpanPanel/simulator`](https://github.com/SpanPanel/simulator).

1. Go to **Settings > Apps** > **App Store** > three-dot menu > **Repositories**
2. Add `https://github.com/SpanPanel/panelbench`
3. Install **SPAN PanelBench** from the store
4. Start the App — a default panel config is included
5. The `span-panel` integration discovers running panels automatically via the Supervisor Discovery API (default configs excluded)
6. Open the web dashboard via **Open Web UI** to configure panels

The App runs the simulator in a container with its own Mosquitto broker. No real SPAN hardware is needed. Each panel runs on its own HTTP port (starting from
`base_http_port`, default 8081) and the dashboard shows the port next to each running panel's serial number.

## Quick Start (macOS standalone)

```bash
# Prerequisites
brew install mosquitto uv

# Run
./scripts/run-local.sh

# Run with debug logging
./scripts/run-local.sh --debug

# Stop / Restart / Status
./scripts/run-local.sh --stop
./scripts/run-local.sh --restart
./scripts/run-local.sh --status
```

The script automatically creates a Python virtual environment, generates TLS certificates, starts Mosquitto (MQTTS on port 18883), and launches the simulator
with mDNS advertising on your LAN IP. No `sudo` required.

Open the dashboard at **<http://localhost:18080>**.

### Multi-panel in standalone mode

Home Assistant's zeroconf auto-discovers **one panel per IP address** (default configs excluded). The first cloned panel appears as a discovery notification in
HA and can be configured normally. Additional panels on the same host need to be added manually — use the port shown in the dashboard panel list:

1. In HA, go to **Settings > Devices & Services > Add Integration**
2. Search for **Span Panel** and enter the host IP and port (e.g. `192.168.1.50` port `8082`)

Each panel has a unique serial number, so there is no conflict between the auto-discovered panel and manually added ones.

## Running with Docker (Linux only)

```bash
docker compose up --build
```

Container-based approaches on macOS do not work for mDNS advertisement. All macOS container runtimes use VM networking that prevents containers from obtaining
real LAN IPs. Use `run-local.sh` on macOS instead.

## Dashboard

The dashboard runs on port 18080 and provides full control over the simulated panel.

### Panel Management

- **Multi-panel** — load multiple YAML configs; click a row to select, start/stop/restart individual panels. Running panels appear as discovered devices in the
  SpanPanel integration (default configs excluded).
- **Clone** — create an editable copy from a template, or from a panel running `r202633+` firmware (IP + passphrase).
- **Model** — open the energy what-if view for a running panel.
- **Purge** — remove recorder history written by the simulated panel's sensors when the simulated panel was added to HA's integration.
- **File operations** — import/export YAML, save & reload
- **Config persistence** — the simulator remembers the last running config across restarts

### Simulation Controls

- **Time-of-day slider** — scrub through the day to see solar curves, time-of-day profiles, and battery schedules respond
- **Speed acceleration** — 1x to 360x time acceleration
- **Grid online/offline** — toggle to test backup behavior and load shedding
- **Islandable toggle** — controls whether PV operates during grid outage
- **Live power chart** — real-time grid, solar, and battery power flows

### Recorder Replay

When connected to Home Assistant, the simulator replays recorded power data from the HA recorder for circuits with mapped entities. This grounds the simulation
in actual household usage patterns rather than synthetic profiles.

```bash
./scripts/run-local.sh --ha-url http://192.168.1.10:8123 --ha-token YOUR_TOKEN
```

Circuits with recorder data show a **REC** badge in the entity list. Clicking the badge toggles to **SYN** (synthetic) mode, where the simulator uses the
configured power profile instead of recorded data. Click again to switch back to recorder replay. This lets you compare how well a synthetic profile matches
your real usage, or override a specific circuit while keeping the rest on recorded data.

### Energy Modeling

The modeling view lets you answer "what if" questions about adding solar or battery storage to your panel. Start from a template or a clone of your own panel, then add or modify PV and
Battery entities to see the projected impact on your grid consumption over historical data.

**Typical workflow:**

1. Start from a template config, or clone your own panel, from the dashboard
2. Connect to HA so circuits replay actual recorded power data
3. Click **Model** on the running panel to enter the modeling view
4. The **Before** chart shows your site power as-is (loads minus any existing solar)
5. Add a Battery entity (or modify an existing one) — adjust capacity, charge/discharge schedule, and backup reserve
6. The **After** chart immediately updates to show grid power with the BESS applied, along with kWh savings
7. Add or resize a PV entity to see how additional solar offsets your consumption in the Before chart
8. Experiment with different battery sizes, charge modes, and PV nameplate ratings — charts auto-refresh on every save

**Modeling controls:**

- **Horizon selector** — last month, 3 months, 6 months, or 1 year
- **Range zoom** — drag the slider to zoom into any time window
- **Circuit overlays** — check individual circuits in the entity list to overlay their power traces on both charts
- **Toggleable legend** — show/hide Solar and Battery traces
- **Energy summary** — net kWh with import/export breakdown and savings percentage

### Entity Management

Add, edit, and delete circuits with specialized editors per type:

- **PV** — nameplate capacity, geographic sine-curve solar model, monthly weather degradation from Open-Meteo historical data
- **Battery** — nameplate capacity (kWh), backup reserve %, charge mode (Custom / Solar Generation / Solar Excess), discharge presets, 24-hour
  charge/discharge/idle schedule
- **EVSE** — charging schedule with presets (Peak Solar, Evening, Night) or custom start/duration, 24-hour visual timeline
- **Circuits** — typical power, 24-hour usage profile with presets, HVAC type selector with seasonal power modulation

PV and Battery are singleton types — only one of each can exist per panel. Recorder-sourced entities preserve their original panel settings (priority, relay
behavior) as read-only.

### Relay Control and Load Shedding

- Click status dots to toggle circuit relays
- Changes from the dashboard or HA integration (via MQTT) are reflected in both directions
- Grid offline triggers load shedding by priority: `OFF_GRID` circuits shed immediately, `SOC_THRESHOLD` circuits shed when battery SOC drops below threshold,
  `NEVER` circuits stay on

### Theme

System, light, or dark theme via the header selector, with localStorage persistence.

## Panel Configuration

Each YAML file in the config directory defines one simulated panel.

### Minimal Example

```yaml
panel_config:
  serial_number: "SPAN-TEST-001"
  total_tabs: 8
  main_size: 100

circuit_templates:
  kitchen:
    energy_profile:
      mode: "consumer"
      power_range: [0.0, 1800.0]
      typical_power: 150.0
      power_variation: 0.3
    relay_behavior: "controllable"
    priority: "NEVER"

circuits:
  - id: "kitchen_outlets"
    name: "Kitchen Outlets"
    template: "kitchen"
    tabs: [1, 3]

unmapped_tabs: [2, 4, 5, 6, 7, 8]

simulation_params:
  update_interval: 5
```

### Config Selection

By default, the simulator loads `default_config.yaml`. To use a different config:

```bash
CONFIG_NAME=simple_test_config.yaml ./scripts/run-local.sh
```

The simulator remembers the last running config and resumes it on restart. When no config is specified and no default exists, all YAML files in the config
directory are loaded.

### Included Configs

| File                                | Tabs | Description                                            |
| ----------------------------------- | ---- | ------------------------------------------------------ |
| `default_config.yaml`               | 40   | Full residential with solar, battery, EVSE             |
| `simple_test_config.yaml`           | 8    | Minimal test: lights, outlets, HVAC, solar             |
| `simulation_config_32_circuit.yaml` | 32   | Full residential with cycling and time-of-day profiles |

## Environment Variables

All variables can also be passed as CLI arguments (`--help` for full list).

| Variable            | Default               | Description                             |
| ------------------- | --------------------- | --------------------------------------- |
| `CONFIG_DIR`        | `./configs`           | Directory containing panel YAML configs |
| `CONFIG_NAME`       | `default_config.yaml` | Specific config file to load            |
| `TICK_INTERVAL`     | `1.0`                 | Seconds between simulation ticks        |
| `LOG_LEVEL`         | `INFO`                | `DEBUG`, `INFO`, `WARNING`, `ERROR`     |
| `HTTP_PORT`         | `8081`                | Bootstrap HTTP server port              |
| `DASHBOARD_PORT`    | `18080`               | Dashboard web UI port                   |
| `BROKER_HOST`       | `localhost`           | MQTT broker hostname                    |
| `BROKER_PORT`       | `18883`               | MQTTS broker port                       |
| `ADVERTISE_ADDRESS` | auto-detected         | IP to advertise via mDNS                |

## Development

See [DEVELOPER.md](DEVELOPER.md) for setup, testing, pre-commit hooks, full config schema, HTTP/MQTT API reference, and simulation engine internals.
