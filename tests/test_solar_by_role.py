"""Solar under a variant that publishes no inverter device: a circuit's feeds role.

No reference capture shows a panel with solar under that variant, so this follows the
eBus catalog's ``connection/feeds-role``: the inverter is no device of its own, and
the circuit feeding it carries the role ``SOLAR`` instead. Outside the fidelity bar,
and marked ``spec_only`` for that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.clone import TYPE_PV, translate_panel_tree
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import discovered_devices, recorded_panel
from tests._helpers import default_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from ebus_sdk import DiscoveredDevice

    from panelbench.config_types import SimulationConfig

pytestmark = [pytest.mark.spec_only, pytest.mark.asyncio]

_SOLAR = "solar_inverter"


def _at_noon_on_solar_role_hardware() -> SimulationConfig:
    """The shipped template on hardware whose variant publishes no inverter device.

    That variant has no commissioned-system circuits, so the template's lock comes
    off the solar circuit, which keeps its locked relay at priority NEVER.
    """
    config = default_config()
    config["hardware_version"] = "3.0"
    for template in config["circuit_templates"].values():
        template.pop("commissioned_system", None)
    config["simulation_params"]["use_simulation_time"] = True
    config["simulation_params"]["simulation_start_time"] = "2026-06-15T12:00:00"
    return config


async def _published(tmp_path: Path, config: SimulationConfig) -> dict[str, DiscoveredDevice]:
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    await emitter_runtime.publish_tick(runtime)
    return discovered_devices(recorder.retained)


async def test_the_inverter_is_no_device_and_its_circuit_feeds_solar(tmp_path: Path) -> None:
    config = _at_noon_on_solar_role_hardware()
    serial = config["panel_config"]["serial_number"]

    devices = await _published(tmp_path, config)

    assert not [d for d in devices.values() if (d.description or {}).get("type") == TYPE_PV]
    circuit = devices[stable_circuit_uuid(serial, _SOLAR)]
    assert circuit.get_property("connection", "feeds-role") == "SOLAR"
    produced = float(circuit.get_property("meter", "active-power") or "0")
    assert produced > 0, "at noon the inverter backfeeds the panel"
    # Booked as the panel's solar flow, which runs out of the inverter.
    pv_flow = float(devices[serial].get_property("power-flows", "pv") or "0")
    assert pv_flow == pytest.approx(-produced, abs=0.1)


async def test_a_clone_keeps_the_circuit_as_the_solar_one(tmp_path: Path) -> None:
    config = _at_noon_on_solar_role_hardware()
    [solar] = [c for c in config["circuits"] if c["id"] == _SOLAR]

    clone = translate_panel_tree(
        config["panel_config"]["serial_number"], await _published(tmp_path, config)
    )

    circuits = clone["circuits"]
    templates = clone["circuit_templates"]
    assert isinstance(circuits, list) and isinstance(templates, dict)
    [cloned] = [c for c in circuits if c["tabs"] == solar["tabs"]]
    assert templates[cloned["template"]]["device_type"] == "pv"
