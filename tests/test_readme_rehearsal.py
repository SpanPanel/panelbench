"""The README's "Rehearsing a SPAN firmware upgrade" procedure, followed on a template.

Each step below is the README's own, applied to a copy of `default_MAIN_40.yaml` as
a template clone copies it. The first config is the panel before the upgrade, with
its "Commissioned PV System" circuit unlocked; the second is the same panel on
release 202639, with that circuit locked and, as "A second inverter" describes, a
load circuit turned into a second inverter. If a step stops producing the panel the
README promises, this fails, rather than a user's rehearsal.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from panelbench.clone import TYPE_PV
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import discovered_devices, recorded_panel
from tests._helpers import default_config, published, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from ebus_sdk import DiscoveredDevice

    from panelbench.config_types import SimulationConfig
    from panelbench.emitter_adapter.runtime import CloneRuntime
    from panelbench.emitter_adapter.wire_capture import RecordingTransport

_ORIGINAL = "solar_inverter"
_NOON = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


def _before() -> SimulationConfig:
    """Step 1, on a clone of a template: its PV circuit plays the "Commissioned PV
    System", renamed with its `id` kept, and its template unlocked."""
    config = default_config()
    [circuit] = [c for c in config["circuits"] if c["id"] == _ORIGINAL]
    circuit["name"] = "Commissioned PV System"
    solar = config["circuit_templates"][circuit["template"]]
    solar["relay_behavior"] = "controllable"
    solar["priority"] = "OFF_GRID"
    return config


def _after(before: SimulationConfig, second: str) -> SimulationConfig:
    """Step 2, then "A second inverter" for the load circuit *second*."""
    config = copy.deepcopy(before)
    config["firmware_version"] = "spanos3/r202639/03"
    templates = config["circuit_templates"]
    [original] = [c for c in config["circuits"] if c["id"] == _ORIGINAL]
    solar = templates[original["template"]]
    solar["commissioned_system"] = "pv"
    solar["priority"] = "NEVER"
    solar["relay_behavior"] = "non-controllable"

    # "Give it a PV template": the commissioned template as the first config has it.
    copied = copy.deepcopy(before["circuit_templates"][original["template"]])
    copied["energy_profile"]["nameplate_capacity_w"] = 3800.0
    copied["energy_profile"]["power_range"] = [-3800.0, 0.0]
    copied["energy_profile"]["typical_power"] = -2280.0
    templates["solar_2"] = copied
    [circuit] = [c for c in config["circuits"] if c["id"] == second]
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
    pv["feed"] = _ORIGINAL
    return config


async def _started(path: Path) -> tuple[CloneRuntime, RecordingTransport]:
    runtime, recorder = await recorded_panel(path)
    await emitter_runtime.publish_tick(runtime)
    return runtime, recorder


def _inverters(recorder: RecordingTransport) -> dict[str, DiscoveredDevice]:
    """Each PV device, keyed by the circuit id feeding it."""
    devices = discovered_devices(recorder.retained)
    return {
        str(device.get_property("info", "feed") or device_id): device
        for device_id, device in devices.items()
        if (device.description or {}).get("type") == TYPE_PV
    }


def _circuit_state(recorder: RecordingTransport, serial: str, circuit_id: str) -> tuple[str, str]:
    device_id = stable_circuit_uuid(serial, circuit_id)
    return (
        published(recorder, device_id, "load-shed/priority"),
        published(recorder, device_id, "switch/relay-controllable"),
    )


@pytest.mark.asyncio
async def test_the_first_config_is_the_panel_before_the_upgrade(tmp_path: Path) -> None:
    runtime, recorder = await _started(write_config(tmp_path / "before.yaml", _before()))

    serial = runtime.engine.serial_number
    assert _circuit_state(recorder, serial, _ORIGINAL) == ("OFF_GRID", "true")
    assert len(_inverters(recorder)) == 1


@pytest.mark.parametrize(
    "second",
    # A load whose override caps its power, and one whose override sets its typical
    # power: whatever the load carried has to go.
    ["new_circuit", "living_room_lights"],
)
@pytest.mark.asyncio
async def test_the_second_config_is_the_same_panel_on_release_202639(
    tmp_path: Path, second: str
) -> None:
    runtime, recorder = await _started(
        write_config(tmp_path / "after.yaml", _after(_before(), second))
    )

    serial = runtime.engine.serial_number
    assert _circuit_state(recorder, serial, _ORIGINAL) == ("NEVER", "false")
    inverters = {
        device.get_property("info", "model"): device for device in _inverters(recorder).values()
    }
    assert set(inverters) == {"IQ8PLUS-72-2-US", "SE3800H-US"}
    assert inverters["IQ8PLUS-72-2-US"].get_property("info", "nominal-power") == "10000.0"
    added = inverters["SE3800H-US"]
    assert added.get_property("info", "nominal-power") == "3800.0"
    assert added.get_property("info", "serial-number") == "sim-inv-0002"

    engine = runtime.engine
    behavior = engine._behavior_engine
    assert behavior is not None
    typical = engine._circuits[second].template["energy_profile"]["typical_power"]
    assert typical == -2280.0, "the inverter's typical power, not the load's, seeds its energy"
    produced = engine._collect_circuit_powers_at_ts(
        _NOON, behavior, {second}, use_recorder_baseline=False
    )[second]
    assert 0 < produced <= 3800.0
