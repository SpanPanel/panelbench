"""Which side of SPAN release 202639 a PanelBench panel publishes.

The emitter reads the panel's `firmware-version` once, at construction. A
`/`-separated segment `r` plus six digits below 202639, such as the shipped
`spanos3/r202633/02`, keeps the earlier conventions; anything else,
including a simulator string such as `sim/v0.1.0`, gets the current ones. Two
conventions differ: the BESS meter's sign, and whether an EVSE's
`config/user-max-charge-current` is published before a user sets it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.firmware import SPAN_RELEASE_202639, predates
from tests._helpers import (
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
    default_config,
    discharging_bess_meter,
    name_firmware,
    night_panel,
)

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.emitter_adapter.runtime import CloneRuntime
    from panelbench.emitter_adapter.wire_capture import RecordingTransport

pytestmark = pytest.mark.asyncio


async def _ticked(tmp_path: Path, firmware: str) -> tuple[CloneRuntime, RecordingTransport]:
    """The default panel at *firmware*, at night, after one tick."""
    config = default_config()
    name_firmware(config, firmware)
    return await night_panel(tmp_path / "panel.yaml", config)


async def _discharging_bess_meter(tmp_path: Path, firmware: str) -> float:
    """The BESS meter's `active-power` of the default panel at *firmware*, discharging."""
    return discharging_bess_meter(*await _ticked(tmp_path, firmware))


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
