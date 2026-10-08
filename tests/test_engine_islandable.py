"""Whether the engine's energy system can island, on a panel with several inverters.

The engine aggregates every producer into one PV source, so one grid-forming
(hybrid) inverter keeps them all producing off-grid, whichever circuit it is on.
The engine used to read only the first producer it met, so the same panel was
islandable or not depending on the order its circuits were listed in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.engine import DynamicSimulationEngine
from tests._helpers import write_config

if TYPE_CHECKING:
    from pathlib import Path


def _inverter(inverter_type: str) -> dict[str, object]:
    return {
        "energy_profile": {
            "mode": "producer",
            "power_range": [-5000.0, 0.0],
            "typical_power": -3000.0,
            "power_variation": 0.1,
            "nameplate_capacity_w": 5000.0,
        },
        "relay_behavior": "non_controllable",
        "priority": "NEVER",
        "device_type": "pv",
        "inverter_type": inverter_type,
    }


def _panel(first: str, second: str) -> dict[str, object]:
    return {
        "panel_config": {"serial_number": "sim-island-0001", "total_tabs": 8, "main_size": 200},
        "circuit_templates": {
            "first_inverter": _inverter(first),
            "second_inverter": _inverter(second),
        },
        "circuits": [
            {"id": "pv_1", "name": "PV 1", "template": "first_inverter", "tabs": [1]},
            {"id": "pv_2", "name": "PV 2", "template": "second_inverter", "tabs": [3]},
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "second", "islandable"),
    [
        ("ac_coupled", "hybrid", True),
        ("hybrid", "ac_coupled", True),
        ("ac_coupled", "ac_coupled", False),
    ],
)
async def test_any_hybrid_inverter_makes_the_panel_islandable(
    tmp_path: Path, first: str, second: str, islandable: bool
) -> None:
    engine = DynamicSimulationEngine(
        config_path=write_config(tmp_path / "panel.yaml", _panel(first, second))
    )
    await engine.initialize_async()

    assert engine.is_grid_islandable is islandable
