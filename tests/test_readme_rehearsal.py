"""The README's "Rehearsing a SPAN firmware upgrade" procedure, followed on a template.

Each step below is the README's own, applied to a copy of `default_MAIN_40.yaml` as
a template clone copies it. The first config is the panel before the upgrade, with
its "Commissioned PV System" circuit unlocked; the second is the same panel on
release 202639, with that circuit locked and, as "A second inverter" describes, a
two-pole circuit turned into a second inverter, its circuit locked the same way: one
the first config already models as a load, or one added to both configs on two free
spaces. If a step stops producing the panel the README promises, this fails, rather
than a user's rehearsal.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from panelbench.clone import TYPE_PV
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import discovered_devices, recorded_panel
from tests._helpers import (
    NOON,
    published,
    write_config,
)
from tests._helpers import (
    REHEARSAL_ADDED_INVERTER as _ADDED,
)
from tests._helpers import (
    REHEARSAL_ORIGINAL_INVERTER as _ORIGINAL,
)
from tests._helpers import (
    rehearsal_after as _after,
)
from tests._helpers import (
    rehearsal_before as _before,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ebus_sdk import DiscoveredDevice

    from panelbench.emitter_adapter.runtime import CloneRuntime
    from panelbench.emitter_adapter.wire_capture import RecordingTransport


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
    # The circuit a template clone adds on two free spaces, and a two-pole load the
    # clone already holds, whose override sets its typical power: whatever the load
    # carried has to go.
    [_ADDED, "main_hvac"],
)
@pytest.mark.asyncio
async def test_the_second_config_is_the_same_panel_on_release_202639(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, second: str
) -> None:
    before = _before(second)
    [first_time] = [c for c in before["circuits"] if c["id"] == second]
    with caplog.at_level(logging.WARNING):
        runtime, recorder = await _started(
            write_config(tmp_path / "after.yaml", _after(before, second))
        )

    serial = runtime.engine.serial_number
    assert _circuit_state(recorder, serial, _ORIGINAL) == ("NEVER", "false")
    # Release 202639 locks the circuit feeding each commissioned inverter.
    assert _circuit_state(recorder, serial, second) == ("NEVER", "false")
    inverters = {
        device.get_property("info", "model"): device for device in _inverters(recorder).values()
    }
    assert set(inverters) == {"IQ8PLUS-72-2-US", "SE3800H-US"}
    assert inverters["IQ8PLUS-72-2-US"].get_property("info", "nominal-power") == "10000.0"
    added = inverters["SE3800H-US"]
    assert added.get_property("info", "nominal-power") == "3800.0"
    assert added.get_property("info", "serial-number") == "sim-inv-0002"

    engine = runtime.engine
    typical = engine.circuit_template(second)["energy_profile"]["typical_power"]
    assert typical == -2280.0, "the inverter's typical power, not the load's, seeds its energy"
    produced = engine.modelled_circuit_power(second, NOON)
    assert 0 < produced <= 3800.0

    # A grid-tied inverter is 240 V on a two-pole breaker, on the same spaces and with
    # the same id in both configs, and is named as an inverter's circuit.
    circuit_device = discovered_devices(recorder.retained)[stable_circuit_uuid(serial, second)]
    spaces = str(circuit_device.get_property("info", "spaces"))
    assert [int(t) for t in spaces.split(",")] == first_time["tabs"]
    assert len(first_time["tabs"]) == 2
    assert (circuit_device.description or {}).get("name") == "Solar Inverter 2"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("one tab" in message for message in warnings)
    assert not any("commissioned_system: pv" in message for message in warnings)
