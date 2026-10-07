"""The engine's read-only view of one circuit: the template it runs and what it models.

A circuit's production scaling with its rating is a public contract, so the tests
that hold it read it through these rather than through the engine's internals.
Both are read-only: reading changes nothing the running panel does next.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from panelbench.emitter_adapter.wire_capture import recorded_panel
from tests._helpers import default_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

_NOON = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


@pytest.mark.asyncio
async def test_a_circuit_template_is_the_one_the_engine_runs_and_a_copy(tmp_path: Path) -> None:
    """With the circuit's overrides applied, and a copy, so a caller cannot re-rate it."""
    runtime, _ = await recorded_panel(write_config(tmp_path / "panel.yaml", default_config()))
    engine = runtime.engine

    template = engine.circuit_template("living_room_lights")
    template["energy_profile"]["typical_power"] = 9999.0

    assert template is not engine.circuit_template("living_room_lights")
    assert engine.circuit_template("living_room_lights")["energy_profile"]["typical_power"] == 50.0


@pytest.mark.asyncio
async def test_modelled_power_is_deterministic_and_reads_nothing_into_the_panel(
    tmp_path: Path,
) -> None:
    """Read for the solar inverter and for the cycling pool pump, whose cycle the
    behaviour engine starts tracking the first time its power is computed."""
    runtime, _ = await recorded_panel(write_config(tmp_path / "panel.yaml", default_config()))
    engine = runtime.engine
    # The live behaviour state is the engine's own; it is read here only to show the
    # accessor leaves it as it was.
    behavior = engine._behavior_engine
    assert behavior is not None
    state = behavior.capture_mutable_state()

    first = engine.modelled_circuit_power("solar_inverter", _NOON)
    second = engine.modelled_circuit_power("solar_inverter", _NOON)
    engine.modelled_circuit_power("pool_pump", _NOON)

    assert first == second > 0
    assert behavior.capture_mutable_state() == state


@pytest.mark.asyncio
async def test_an_unknown_circuit_is_a_key_error(tmp_path: Path) -> None:
    runtime, _ = await recorded_panel(write_config(tmp_path / "panel.yaml", default_config()))

    with pytest.raises(KeyError):
        runtime.engine.circuit_template("no_such_circuit")
    with pytest.raises(KeyError):
        runtime.engine.modelled_circuit_power("no_such_circuit", _NOON)
