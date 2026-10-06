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
from panelbench.emitter_adapter.wire_capture import RecordingTransport, recorded_panel
from panelbench.firmware import SPAN_RELEASE_202639, predates
from tests._helpers import (
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
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
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    await emitter_runtime.publish_tick(runtime)
    return runtime, recorder


async def _discharging_bess_meter(tmp_path: Path, firmware: str) -> float:
    """The BESS meter's `active-power` of the default panel at *firmware*, discharging."""
    runtime, recorder = await _ticked(tmp_path, firmware)
    battery = bess_config_from_engine(runtime.engine)
    assert battery is not None

    meter = float(published(recorder, battery.instance_id, "meter/active-power"))
    flow = float(published(recorder, runtime.engine.serial_number, "power-flows/battery"))

    # An idle battery shows no frame at all, so no caller may pass on one.
    assert flow < 0, "the battery discharges at night, so power flows out of it"
    return meter


@pytest.mark.parametrize(
    ("firmware", "same_sign"), [(EARLIER_FIRMWARE, True), (CURRENT_FIRMWARE, False)]
)
async def test_the_bess_meter_frame_follows_the_firmware(
    tmp_path: Path, firmware: str, same_sign: bool
) -> None:
    meter = await _discharging_bess_meter(tmp_path, firmware)

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


@pytest.mark.parametrize(
    "firmware",
    ["spanos2/r202638/01", CURRENT_FIRMWARE, "sim/v0.1.0", "spanos2/r2026390/01"],
)
async def test_panelbench_reads_the_release_as_the_emitter_publishes_it(
    tmp_path: Path, firmware: str
) -> None:
    """PanelBench's copy of the release rule must agree with the emitter's, on the wire.

    The emitter's rule is not public API, so PanelBench keeps its own for the PV
    gate and the REST status. This reads which frame the emitter actually chose
    for the BESS meter and fails the moment the two rules disagree on a string.
    """
    meter = await _discharging_bess_meter(tmp_path, firmware)

    emitter_publishes_earlier = meter < 0
    assert predates(firmware, SPAN_RELEASE_202639) is emitter_publishes_earlier
