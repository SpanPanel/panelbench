"""What the tests that run a panel through the emitter share.

A panel here is the shipped default config, optionally modified and written to a
temporary file, then started by `wire_capture.recorded_panel`. That goes through
the assembly a real panel does, so what the recorder holds is what a consumer
would replay from the broker.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import yaml
from ebus_panel_sim.relay_resolver import RelayRequester, RelayState

from panelbench.clone import TYPE_PV, translate_panel_tree
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.runtime import bess_config_from_engine
from panelbench.emitter_adapter.wire_capture import (
    capture_retained,
    discovered_devices,
    recorded_panel,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ebus_sdk import DiscoveredDevice

    from panelbench.config_types import SimulationConfig
    from panelbench.emitter_adapter.runtime import CloneRuntime
    from panelbench.emitter_adapter.wire_capture import RecordingTransport

DEFAULT_CONFIG: Final = Path(__file__).resolve().parents[1] / "configs" / "default_MAIN_40.yaml"

# SPAN firmware strings either side of release 202639, where the BESS meter's sign
# and the EVSE user limit's publication changed.
EARLIER_FIRMWARE: Final = "spanos2/r202633/02"
CURRENT_FIRMWARE: Final = "spanos2/r202639/01"

# Midday in June where the shipped panels sit, so an inverter there is producing.
NOON: Final = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


def default_config() -> SimulationConfig:
    """A fresh copy of the shipped default config, to modify and then write."""
    config: SimulationConfig = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    return config


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
