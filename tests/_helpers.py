"""What the tests that run a panel through the emitter share.

A panel here is `PANEL_CONFIG`, optionally modified and written to a temporary
file, then started by `wire_capture.recorded_panel`. That goes through
the assembly a real panel does, so what the recorder holds is what a consumer
would replay from the broker.
"""

from __future__ import annotations

import asyncio
import copy
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import yaml
from ebus_panel_sim import Tree, load_reference_capture
from ebus_panel_sim.relay_resolver import RelayRequester, RelayState
from ebus_sdk import DiscoveredDevice

from panelbench.clone import TYPE_PV, translate_panel_tree
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.runtime import bess_config_from_engine
from panelbench.emitter_adapter.wire_capture import (
    capture_retained,
    discovered_devices,
    recorded_panel,
)
from panelbench.firmware import SPAN_RELEASE_202639, predates
from panelbench.recorder import RecorderDataSource
from panelbench.reference_templates import (
    template_capture_names,
    template_filename,
    write_reference_templates,
)
from panelbench.sqlite_history import SCHEMA_SQL, SqliteHistoryProvider
from tests.fidelity.tree_diff import retained_topics

if TYPE_CHECKING:
    from collections.abc import Mapping

    from panelbench.config_types import SimulationConfig
    from panelbench.emitter_adapter.runtime import CloneRuntime
    from panelbench.emitter_adapter.wire_capture import RecordingTransport

CONFIGS: Final = Path(__file__).resolve().parents[1] / "configs"
# The working panel the tests run: a tracked template with modelled loads, PV, a
# battery and a SPAN Drive, so every power flow has something to carry.
PANEL_CONFIG: Final = CONFIGS / "ref_MAIN_32_TANDEMS.yaml"

# The hand-written ``ref_`` templates, tracked in git beside the ones generated at start.
SHIPPED_TEMPLATES: Final = ("ref_MAIN_32_TANDEMS.yaml", "ref_MLO_48_TANDEMS.yaml")

# Every template a config directory holds once PanelBench has started: the tracked ones,
# and one built from each of the pinned emitter's reference captures. Named, never
# globbed from `CONFIGS`, which holds the generated ones in a checkout and not in CI.
EVERY_TEMPLATE: Final = (
    *SHIPPED_TEMPLATES,
    *(template_filename(name) for name in template_capture_names()),
)

# The pinned emitter release's masked capture of a real MAIN 32 on SPAN release 202639,
# by its name among the release's reference captures.
CAPTURED_MAIN_32: Final = "main32_r202639"
CAPTURED_MAIN_32_SERIAL: Final = "nt-9874-s7rxt"

# SPAN firmware strings either side of release 202639, where the BESS meter's sign
# and the EVSE user limit's publication changed.
EARLIER_FIRMWARE: Final = "spanos2/r202633/02"
CURRENT_FIRMWARE: Final = "spanos2/r202639/01"

# Midday in June where the shipped panels sit, so an inverter there is producing.
NOON: Final = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


def panel_config() -> SimulationConfig:
    """A fresh copy of `PANEL_CONFIG`, to modify and then write."""
    config: SimulationConfig = yaml.safe_load(PANEL_CONFIG.read_text(encoding="utf-8"))
    return config


def lay_every_template(config_dir: Path) -> None:
    """Lay `EVERY_TEMPLATE` into *config_dir* as a start does: the tracked ones as
    ``run.sh`` seeds them, the generated ones as ``write_reference_templates`` builds them."""
    for name in SHIPPED_TEMPLATES:
        (config_dir / name).write_bytes((CONFIGS / name).read_bytes())
    write_reference_templates(config_dir)
    # A capture the emitter cannot import is logged and skipped at a start; here it
    # would leave a test checking fewer templates than it names.
    missing = [name for name in EVERY_TEMPLATE if not (config_dir / name).is_file()]
    assert not missing, f"not laid: {missing}"


# `PANEL_CONFIG` has one free space. A test that needs two takes out this two-pole
# circuit, whose load no test reads, as the README's rehearsal frees them.
_FREED_CIRCUIT: Final = "heat_pump_backup"

# The circuit of the panel's one SPAN Drive, whose template a second takes.
_FIRST_DRIVE: Final = "span_drive_garage"

# The second SPAN Drive a test of several drives adds beside the panel's one.
SECOND_DRIVE: Final = "SPAN Drive - Driveway"


def free_two_spaces(config: SimulationConfig) -> list[int]:
    """Free two of *config*'s spaces, one above the other, and return them.

    The circuit on them comes out and they join ``unmapped_tabs``, so *config* is
    the same panel with two more spaces free.
    """
    [circuit] = [c for c in config["circuits"] if c["id"] == _FREED_CIRCUIT]
    config["circuits"].remove(circuit)
    spaces = list(circuit["tabs"])
    assert len(spaces) == 2 and spaces[1] - spaces[0] == 2, (
        f"two poles, one above the other: {spaces}"
    )
    config["unmapped_tabs"] = sorted([*config["unmapped_tabs"], *spaces])
    return spaces


def add_second_drive(config: SimulationConfig) -> None:
    """Give *config* a second SPAN Drive, `SECOND_DRIVE`, after its own.

    On the drive template the first uses, on two spaces `free_two_spaces` frees.
    """
    [first] = [c for c in config["circuits"] if c["id"] == _FIRST_DRIVE]
    spaces = free_two_spaces(config)
    config["circuits"].append(
        {
            "id": "span_drive_driveway",
            "name": SECOND_DRIVE,
            "template": first["template"],
            "tabs": spaces,
        }
    )
    config["unmapped_tabs"] = [t for t in config["unmapped_tabs"] if t not in spaces]


def name_firmware(config: SimulationConfig, firmware: str) -> None:
    """Make *config* name *firmware*, as a user emulating that release edits a clone.

    The shipped templates lock their solar circuit as release 202639 does, and a
    config naming an earlier release is refused if it asks for that lock, so the
    lock comes off with the earlier firmware, as the docs tell a user to.
    """
    config["firmware_version"] = firmware
    if predates(firmware, SPAN_RELEASE_202639):
        for template in config["circuit_templates"].values():
            template.pop("commissioned_system", None)


def write_config(path: Path, config: Mapping[str, object]) -> Path:
    """Write *config* to *path* as YAML and return *path*."""
    path.write_text(yaml.safe_dump(dict(config), sort_keys=False), encoding="utf-8")
    return path


async def source_and_clone(
    tmp_path: Path, source: SimulationConfig
) -> tuple[dict[str, DiscoveredDevice], dict[str, DiscoveredDevice]]:
    """What *source* publishes, and what a clone of it publishes.

    The clone reads *source*'s published tree through the translator a live clone
    uses, so whatever it drops is dropped from a clone of a real panel too.
    """
    source_path = write_config(tmp_path / "panel.yaml", source)
    devices = discovered_devices(await capture_retained(source_path))
    cloned = translate_panel_tree(source["panel_config"]["serial_number"], devices)
    clone_path = write_config(tmp_path / "clone.yaml", cloned)
    clone = discovered_devices(await capture_retained(clone_path))
    return devices, clone


def published(recorder: RecordingTransport, device_id: str, path: str) -> str:
    """The retained value of *device_id*'s property at *path*."""
    return recorder.retained[f"ebus/5/{device_id}/{path}"].decode()


async def night_panel(
    path: Path, config: SimulationConfig
) -> tuple[CloneRuntime, RecordingTransport]:
    """The panel *config* describes, written to *path*, at night after one tick.

    Night, so the battery is discharging to cover load and its sign is observable.
    The shipped batteries are self-consumption, which ignores `discharge_hours`: it
    discharges whenever load exceeds PV and its charge is above the reserve.
    *config* itself is left unchanged.
    """
    night = copy.deepcopy(config)
    night["simulation_params"]["use_simulation_time"] = True
    night["simulation_params"]["simulation_start_time"] = "2026-06-15T22:00:00"
    runtime, recorder = await recorded_panel(write_config(path, night))
    await emitter_runtime.publish_tick(runtime)
    return runtime, recorder


def discharging_bess_meter(runtime: CloneRuntime, recorder: RecordingTransport) -> float:
    """The BESS meter's `active-power` of a `night_panel`, whose battery is discharging."""
    battery = bess_config_from_engine(runtime.engine)
    assert battery is not None

    meter = float(published(recorder, battery.instance_id, "meter/active-power"))
    flow = float(published(recorder, runtime.engine.serial_number, "power-flows/battery"))

    # An idle battery shows no frame at all, so no caller may pass on one.
    assert flow < 0, "the battery discharges at night, so power flows out of it"
    return meter


def rating_literal(watts: float) -> str:
    """*watts* as a SPAN panel publishes an inverter's ``info/nominal-power``: an integer."""
    return f"{watts:.0f}"


async def pv_rating(path: Path, circuit_id: str = "solar_inverter") -> tuple[str | None, float]:
    """The panel at *path*'s one PV device's published ``info/nominal-power``, and what
    its PV circuit *circuit_id* produces at `NOON`.

    Both, because a rating is a contract on the wire and on the power: the bug the
    callers pin was a panel that published one rating and produced at another.
    """
    runtime, recorder = await recorded_panel(path)
    await emitter_runtime.publish_tick(runtime)
    devices = discovered_devices(recorder.retained)
    [pv] = [d for d in devices.values() if (d.description or {}).get("type") == TYPE_PV]
    produced = runtime.engine.modelled_circuit_power(circuit_id, NOON)
    assert produced > 0, "noon in June, so the inverter is producing"
    return pv.get_property("info", "nominal-power"), produced


def settable_relays(runtime: CloneRuntime) -> list[str]:
    """Circuits a relay command may move: every one not locked by commissioning."""
    return [
        uuid
        for uuid in runtime.uuid_to_circuit_id
        if runtime.emitter.relays.state(uuid)[1] != RelayRequester.CONFIGURATION
    ]


async def relay_opened_by_command(
    runtime: CloneRuntime, uuid: str, *, within: float = 2.0
) -> None:
    """Wait until circuit *uuid*'s relay is open on a user's command."""
    async with asyncio.timeout(within):
        while runtime.emitter.relays.state(uuid) != (RelayState.OPEN, RelayRequester.USER):
            await asyncio.sleep(0.005)


def captured_main_32() -> Tree:
    """The tree the captured MAIN 32 published, as the pinned release ships it."""
    return load_reference_capture(CAPTURED_MAIN_32).tree


def discovered_from_tree(tree: Tree) -> dict[str, DiscoveredDevice]:
    """A captured tree as the devices a scrape of that panel would discover."""
    return discovered_devices(retained_topics(tree))


REHEARSAL_ORIGINAL_INVERTER: Final = "solar_inverter"
# The README's "Rehearsing a SPAN firmware upgrade", on a copy of `PANEL_CONFIG`:
# shared by the test that follows the README and the fidelity round trip, so both
# read one recipe. The second inverter's circuit is the one "A second inverter" adds
# to a clone of a template: two-pole, on two spaces it frees, on the load template
# it names.
REHEARSAL_ADDED_INVERTER: Final = "solar_inverter_2"
_REHEARSAL_LOAD_TEMPLATE: Final = "outlets"


def rehearsal_before(second: str | None = None) -> SimulationConfig:
    """Step 1, on a clone of a template: named back to release 202633, its
    "Commissioned PV System" circuit's template unlocked as that release has it.

    With *second* `REHEARSAL_ADDED_INVERTER`, the second inverter's two-pole circuit is
    added too, as a load, on two spaces freed for it and taken out of `unmapped_tabs`.
    """
    config = panel_config()
    name_firmware(config, "spanos3/r202633/02")
    [circuit] = [c for c in config["circuits"] if c["id"] == REHEARSAL_ORIGINAL_INVERTER]
    assert circuit["name"] == "Commissioned PV System"
    solar = config["circuit_templates"][circuit["template"]]
    solar["relay_behavior"] = "controllable"
    solar["priority"] = "OFF_GRID"
    if second == REHEARSAL_ADDED_INVERTER:
        spaces = free_two_spaces(config)
        config["circuits"].append(
            {
                "id": REHEARSAL_ADDED_INVERTER,
                "name": "Solar Inverter 2",
                "template": _REHEARSAL_LOAD_TEMPLATE,
                "tabs": spaces,
                "breaker_rating": 20,
            }
        )
        config["unmapped_tabs"] = [t for t in config["unmapped_tabs"] if t not in spaces]
    return config


def rehearsal_after(before: SimulationConfig, second: str) -> SimulationConfig:
    """Step 2, then "A second inverter" for the two-pole circuit *second*."""
    config = copy.deepcopy(before)
    config["firmware_version"] = "spanos3/r202639/03"
    templates = config["circuit_templates"]
    [original] = [c for c in config["circuits"] if c["id"] == REHEARSAL_ORIGINAL_INVERTER]
    solar = templates[original["template"]]
    solar["commissioned_system"] = "pv"
    solar["priority"] = "NEVER"
    solar["relay_behavior"] = "non-controllable"

    # "Give it a commissioned PV template": the commissioned template as this config
    # has it, locked.
    copied = copy.deepcopy(solar)
    copied["energy_profile"]["nameplate_capacity_w"] = 3800.0
    copied["energy_profile"]["power_range"] = [-3800.0, 0.0]
    copied["energy_profile"]["typical_power"] = -2280.0
    templates["solar_2"] = copied
    [circuit] = [c for c in config["circuits"] if c["id"] == second]
    # "Name the circuit as its inverter's."
    circuit["name"] = "Solar Inverter 2"
    circuit["template"] = "solar_2"
    # "Remove the circuit's own `overrides`."
    circuit.pop("overrides", None)
    # "Give the circuit ... its own inverter's vendor, model and serial_number."
    circuit["vendor"] = "SolarEdge"
    circuit["model"] = "SE3800H-US"
    circuit["serial_number"] = "sim-inv-0002"
    # "Where the config has a top-level `pv` section ... set `pv.feed`."
    pv = config.get("pv")
    assert pv is not None
    pv["feed"] = REHEARSAL_ORIGINAL_INVERTER
    return config


async def recorder_history(tmp_path: Path, entity: str) -> RecorderDataSource:
    """A recorder holding 25 hourly means of *entity*, the last a week ago, as a
    panel's history database gives them."""
    db_path = tmp_path / "panel_history.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA_SQL)
    con.execute(
        "INSERT INTO statistics_meta (id, statistic_id, unit_of_measurement) VALUES (1, ?, 'W')",
        (entity,),
    )
    end = time.time() - 7 * 86400
    end -= end % 3600
    for hour in range(25):
        ts = end - (24 - hour) * 3600
        mean = 100.0 + 5.0 * hour
        con.execute(
            "INSERT INTO statistics (metadata_id, created_ts, start_ts, mean, min, max) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            (ts, ts, mean, mean * 0.9, mean * 1.1),
        )
    con.commit()
    con.close()
    recorder = RecorderDataSource()
    assert await recorder.load(SqliteHistoryProvider(db_path), [entity], lookback_days=365) == 1
    return recorder
