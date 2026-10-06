"""Which side of SPAN release 202639 a PanelBench panel publishes.

The emitter reads the panel's `firmware-version` once, at construction. A
`/`-separated segment `r` plus six digits below 202639 keeps the earlier
conventions; anything else, including the shipped configs' `sim/v0.1.0`, gets
the current ones. Two conventions differ: the BESS meter's sign, and whether an
EVSE's `config/user-max-charge-current` is published before a user sets it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.runtime import CloneRuntime, bess_config_from_engine
from panelbench.emitter_adapter.wire_capture import RecordingTransport
from tests._helpers import (
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
    clone,
    default_config,
    published,
    write_config,
)

pytestmark = pytest.mark.asyncio


async def _ticked(tmp_path: Path, firmware: str) -> tuple[CloneRuntime, RecordingTransport]:
    """The default panel at *firmware*, at night, after one tick."""
    config = default_config()
    config["firmware_version"] = firmware
    # Night, so the battery is discharging to cover load and its sign is observable.
    # The default battery is self-consumption, which ignores `discharge_hours`: it
    # discharges whenever load exceeds PV and its charge is above the reserve.
    config["simulation_params"]["use_simulation_time"] = True
    config["simulation_params"]["simulation_start_time"] = "2026-06-15T22:00:00"
    runtime, recorder = await clone(write_config(tmp_path / "panel.yaml", config))
    await emitter_runtime.publish_tick(runtime)
    return runtime, recorder


@pytest.mark.parametrize(
    ("firmware", "same_sign"), [(EARLIER_FIRMWARE, True), (CURRENT_FIRMWARE, False)]
)
async def test_the_bess_meter_frame_follows_the_firmware(
    tmp_path: Path, firmware: str, same_sign: bool
) -> None:
    runtime, recorder = await _ticked(tmp_path, firmware)
    battery = bess_config_from_engine(runtime.engine)
    assert battery is not None

    meter = float(published(recorder, battery.instance_id, "meter/active-power"))
    flow = float(published(recorder, runtime.engine.serial_number, "power-flows/battery"))

    assert flow < 0, "the battery discharges at night, so power flows out of it"
    assert (meter < 0) if same_sign else (meter > 0)


@pytest.mark.parametrize(
    ("firmware", "preset"), [(EARLIER_FIRMWARE, True), (CURRENT_FIRMWARE, False)]
)
async def test_the_evse_limit_is_preset_only_before_202639(
    tmp_path: Path, firmware: str, preset: bool
) -> None:
    _runtime, recorder = await _ticked(tmp_path, firmware)

    limits = [t for t in recorder.retained if t.endswith("/config/user-max-charge-current")]

    assert bool(limits) is preset
