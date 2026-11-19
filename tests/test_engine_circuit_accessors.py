"""The engine's read-only view of one circuit: the template it runs and what it models.

A circuit's production scaling with its rating is a public contract, so the tests
that hold it read it through these rather than through the engine's internals.
Both are read-only: one returns a copy, and the other reads a behaviour engine that
keeps no state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.emitter_adapter.wire_capture import recorded_panel
from tests._helpers import NOON, panel_config, write_config

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_a_circuit_template_is_the_one_the_engine_runs_and_a_copy(tmp_path: Path) -> None:
    """With the circuit's overrides applied, and a copy, so a caller cannot re-rate it."""
    runtime, _ = await recorded_panel(write_config(tmp_path / "panel.yaml", panel_config()))
    engine = runtime.engine

    template = engine.circuit_template("living_room_lights")
    template["energy_profile"]["typical_power"] = 9999.0

    assert template is not engine.circuit_template("living_room_lights")
    assert engine.circuit_template("living_room_lights")["energy_profile"]["typical_power"] == 45.0


@pytest.mark.asyncio
async def test_modelled_power_is_deterministic(tmp_path: Path) -> None:
    """Read twice for the solar inverter and for the cycling dishwasher: each reads
    the same both times."""
    runtime, _ = await recorded_panel(write_config(tmp_path / "panel.yaml", panel_config()))
    engine = runtime.engine

    first = engine.modelled_circuit_power("solar_inverter", NOON)
    second = engine.modelled_circuit_power("solar_inverter", NOON)
    dishwasher = engine.modelled_circuit_power("dishwasher", NOON)

    assert first == second > 0
    assert engine.modelled_circuit_power("dishwasher", NOON) == dishwasher


@pytest.mark.asyncio
async def test_an_unknown_circuit_is_a_key_error(tmp_path: Path) -> None:
    runtime, _ = await recorded_panel(write_config(tmp_path / "panel.yaml", panel_config()))

    with pytest.raises(KeyError):
        runtime.engine.circuit_template("no_such_circuit")
    with pytest.raises(KeyError):
        runtime.engine.modelled_circuit_power("no_such_circuit", NOON)
