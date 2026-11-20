"""The Model view's data: a Before and an After pass over the recorder's history.

The After pass models each circuit as ``modelled_circuit_power`` does, cycling loads
included, and both passes read the live behaviour engine, which keeps no state: so
the data is the same each time it is computed, and computing it draws nothing the
live panel draws on next.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Final

import pytest

from panelbench.engine import DynamicSimulationEngine
from tests._helpers import panel_config, recorder_history

if TYPE_CHECKING:
    from pathlib import Path

_ENTITY: Final = "sensor.synthetic_circuit_power"


@pytest.mark.asyncio
async def test_the_model_view_s_after_pass_is_each_circuit_s_model_and_repeats(
    tmp_path: Path,
) -> None:
    config = panel_config()
    config["circuits"][0]["recorder_entity"] = _ENTITY
    engine = DynamicSimulationEngine(
        config_data=config, recorder=await recorder_history(tmp_path, _ENTITY)
    )
    await engine.initialize_async()
    circuits = [c["id"] for c in config["circuits"]]
    assert any("cycling_pattern" in engine.circuit_template(cid) for cid in circuits)
    live = random.getstate()

    first = await engine.compute_modeling_data(24)
    second = await engine.compute_modeling_data(24)

    assert "error" not in first, first
    assert len(first["timestamps"]) == 25
    assert first == second
    assert random.getstate() == live
    for cid in circuits:
        assert first["circuits"][cid]["power"] == [
            round(engine.modelled_circuit_power(cid, float(ts)), 1) for ts in first["timestamps"]
        ], cid
