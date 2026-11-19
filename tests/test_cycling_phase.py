"""A cycling load's on/off phase follows simulated time.

The engine timed a cycle from the moment the run started on the real clock, so one
config at one simulated moment read differently from one run to the next: a load
due on read 0 W whenever the run began inside its off phase.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import recorded_panel
from tests._helpers import panel_config, published, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.config_types import SimulationConfig

_ON_S: Final = 2400
_OFF_S: Final = 300
_CYCLE_S: Final = _ON_S + _OFF_S

# An afternoon in June where the shipped panels sit, 900 s into a cycle of simulated
# time: its timestamp is a whole number of cycles and 900 s.
_AFTERNOON: Final = datetime(2026, 6, 15, 15, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

# A moment in November 2026 on the real clock, near when these tests were written.
_NOVEMBER: Final = 1_795_000_000.0


def _cycling(config: SimulationConfig) -> str:
    """Make *config*'s first circuit, whichever it is, a steady 1000 W load that cycles
    on for 2400 s and off for 300 s, and return its id."""
    circuit = config["circuits"][0]
    config["circuit_templates"]["cycling_load"] = {
        "energy_profile": {
            "mode": "consumer",
            "power_range": [0.0, 2000.0],
            "typical_power": 1000.0,
            "power_variation": 0.0,
        },
        "relay_behavior": "controllable",
        "priority": "NEVER",
        "breaker_rating": 15,
        "cycling_pattern": {"on_duration": _ON_S, "off_duration": _OFF_S},
    }
    circuit["template"] = "cycling_load"
    circuit.pop("overrides", None)
    return circuit["id"]


async def _reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: datetime, real_start: float
) -> float:
    """The cycling load's power after one tick at simulated *at*, the run having begun at
    *real_start* on the real clock."""
    config = panel_config()
    circuit_id = _cycling(config)
    config["simulation_params"]["use_simulation_time"] = True
    config["simulation_params"]["simulation_start_time"] = at.isoformat()
    real = time.time
    began = real()
    with monkeypatch.context() as patch:
        patch.setattr(time, "time", lambda: real_start + (real() - began))
        runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
        await emitter_runtime.publish_tick(runtime)
    uuid = stable_circuit_uuid(config["panel_config"]["serial_number"], circuit_id)
    return float(published(recorder, uuid, "meter/active-power"))


def _real_start(offset_s: float) -> float:
    """A real start in November 2026 that the old rule put *offset_s* into the cycle at
    ``_AFTERNOON``: ``(_AFTERNOON - start) % _CYCLE_S == offset_s``."""
    base = _AFTERNOON.timestamp() - offset_s
    return base + round((_NOVEMBER - base) / _CYCLE_S) * _CYCLE_S


@pytest.mark.asyncio
async def test_a_cycling_load_reads_the_same_at_one_simulated_moment_whenever_the_run_begins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs at the same simulated afternoon, begun 2400 s apart on the real clock,
    where the old rule put one 2500 s into its cycle, in the off phase, and the other
    100 s in."""
    first = await _reading(tmp_path, monkeypatch, _AFTERNOON, _real_start(2500))
    second = await _reading(tmp_path, monkeypatch, _AFTERNOON, _real_start(100))

    assert abs(first) > 0
    assert first == pytest.approx(second, rel=0.05)


@pytest.mark.parametrize(
    ("into_cycle_s", "on"),
    [
        pytest.param(100, True, id="100 s into a cycle, on"),
        pytest.param(2300, True, id="2300 s in, on"),
        pytest.param(2500, False, id="2500 s in, off"),
    ],
)
@pytest.mark.asyncio
async def test_a_cycling_load_is_off_for_the_last_off_duration_of_each_cycle_of_simulated_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, into_cycle_s: int, on: bool
) -> None:
    """The load is off for the last 300 s of each 2700 s of simulated time, counted from
    the epoch, however the run began: at the first tick of a run begun there too."""
    at = _AFTERNOON + timedelta(seconds=into_cycle_s - 900)
    assert at.timestamp() % _CYCLE_S == into_cycle_s

    power = await _reading(tmp_path, monkeypatch, at, _NOVEMBER)

    assert (abs(power) > 0) is on


@pytest.mark.parametrize(
    ("into_cycle_s", "on"),
    [
        pytest.param(0, True, id="at a cycle's start, on"),
        pytest.param(2399, True, id="2399 s in, on"),
        pytest.param(2400, False, id="2400 s in, off"),
        pytest.param(2699, False, id="2699 s in, off"),
        pytest.param(2700, True, id="the next cycle's start, on"),
    ],
)
@pytest.mark.asyncio
async def test_the_model_view_reads_a_cycling_load_on_for_exactly_its_on_duration(
    tmp_path: Path, into_cycle_s: int, on: bool
) -> None:
    """``modelled_circuit_power``, the Model view's path, adds no noise, so it pins the
    boundary: on from the start of each 2700 s cycle of simulated time, off from
    2400 s in, at any moment the model asks about."""
    config = panel_config()
    circuit_id = _cycling(config)
    runtime, _ = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    cycle_start = _AFTERNOON.timestamp() - 900
    assert cycle_start % _CYCLE_S == 0

    power = runtime.engine.modelled_circuit_power(circuit_id, cycle_start + into_cycle_s)

    assert abs(power) == (1000.0 if on else 0.0)
