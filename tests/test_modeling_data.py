"""The Model view's data: a Before and an After pass over the recorder's history.

The After pass models each circuit as ``modelled_circuit_power`` does, cycling loads
included, and both passes read the live behaviour engine, which keeps no state: so
the data is the same each time it is computed, and computing it draws nothing the
live panel draws on next.
"""

from __future__ import annotations

import random
import sqlite3
import time
from typing import TYPE_CHECKING, Final

import pytest

from panelbench.engine import DynamicSimulationEngine
from panelbench.recorder import RecorderDataSource
from panelbench.sqlite_history import SCHEMA_SQL, SqliteHistoryProvider
from tests._helpers import panel_config

if TYPE_CHECKING:
    from pathlib import Path

_ENTITY: Final = "sensor.synthetic_circuit_power"


async def _history(tmp_path: Path) -> RecorderDataSource:
    """A recorder holding 25 hourly means of ``_ENTITY``, the last a week ago, as a
    panel's history database gives them."""
    db_path = tmp_path / "panel_history.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA_SQL)
    con.execute(
        "INSERT INTO statistics_meta (id, statistic_id, unit_of_measurement) VALUES (1, ?, 'W')",
        (_ENTITY,),
    )
    end = time.time() - 7 * 86400
    end -= end % 3600
    for hour in range(25):
        ts = end - (24 - hour) * 3600
        mean = 100.0 + 5.0 * hour
        con.execute(
            "INSERT INTO statistics (metadata_id, created_ts, start_ts, mean, min, max) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            (ts, ts, mean, mean * 0.9, mean * 1.1),
        )
    con.commit()
    con.close()
    recorder = RecorderDataSource()
    assert await recorder.load(SqliteHistoryProvider(db_path), [_ENTITY], lookback_days=365) == 1
    return recorder


@pytest.mark.asyncio
async def test_the_model_view_s_after_pass_is_each_circuit_s_model_and_repeats(
    tmp_path: Path,
) -> None:
    config = panel_config()
    config["circuits"][0]["recorder_entity"] = _ENTITY
    engine = DynamicSimulationEngine(config_data=config, recorder=await _history(tmp_path))
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
