"""A hybrid battery takes its own solar.

``bess.inverter_type: hybrid`` says the battery's own inverter also takes PV, on its DC
side, and ``bess.pv_array.nameplate_capacity_w`` rates the array it takes. The engine
produces that array with the model PV circuits use, the panel's location and weather,
and hands it to the emitter as the battery's PV (``TickInputs.bess_pv``), which adds it
to ``power-flows/pv`` and to what the battery's dispatch sees. The battery is still
dispatched by the emitter. A clone reads the coupling as the emitter's capture reads it,
from what the tree publishes, and an import keeps its definition's.
"""

from __future__ import annotations

import copy
import random
from typing import TYPE_CHECKING, Final

import pytest
import yaml
from ebus_panel_sim import load_reference_capture, reference_capture_names

from panelbench.clone import translate_panel_tree
from panelbench.definition_import import config_from_definition
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.wire_capture import recorded_panel
from panelbench.engine import DynamicSimulationEngine
from panelbench.validation import validate_yaml_config
from tests._helpers import (
    discovered_from_tree,
    panel_config,
    published,
    recorder_history,
    write_config,
)

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.config_types import SimulationConfig

_NOON: Final = "2026-06-15T12:00:00"
_MIDNIGHT: Final = "2026-06-15T00:00:00"
_ARRAY_W: Final = 7600.0

# The captures whose battery the emitter's capture writes hybrid: in each, the panel
# publishes power-flows/pv and nothing but its one battery could bring it solar.
_HYBRID_CAPTURES: Final = frozenset({"r202639-c", "r202639-f", "r202645-g"})


def _hybrid(*, nameplate_w: float | None = _ARRAY_W) -> SimulationConfig:
    """The working panel with its battery hybrid, its array rated *nameplate_w*."""
    config = panel_config()
    bess = config["bess"]
    bess["inverter_type"] = "hybrid"
    if nameplate_w is not None:
        bess["pv_array"] = {"nameplate_capacity_w": nameplate_w}
    return config


async def _flows_at(tmp_path: Path, config: SimulationConfig, at: str) -> dict[str, float]:
    """The panel's four power flows after one tick at simulated *at*."""
    timed = copy.deepcopy(config)
    timed["simulation_params"]["use_simulation_time"] = True
    timed["simulation_params"]["simulation_start_time"] = at
    # The circuits' noise is drawn from `random`, and the array draws none, so two runs
    # compared with each other draw the same.
    random.seed(20261119)
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", timed))
    await emitter_runtime.publish_tick(runtime)
    serial = config["panel_config"]["serial_number"]
    return {
        flow: float(published(recorder, serial, f"power-flows/{flow}"))
        for flow in ("site", "grid", "pv", "battery")
    }


@pytest.mark.asyncio
async def test_at_noon_a_hybrid_battery_s_array_joins_the_solar_flow(tmp_path: Path) -> None:
    """``power-flows/pv`` is negative while producing, so the array makes it more so."""
    plain = await _flows_at(tmp_path, panel_config(), _NOON)
    hybrid = await _flows_at(tmp_path, _hybrid(), _NOON)

    assert hybrid["pv"] < plain["pv"]
    assert plain["pv"] - hybrid["pv"] <= _ARRAY_W
    assert abs(sum(hybrid.values())) <= 1.5, hybrid


@pytest.mark.asyncio
async def test_at_midnight_the_array_produces_nothing(tmp_path: Path) -> None:
    plain = await _flows_at(tmp_path, panel_config(), _MIDNIGHT)
    hybrid = await _flows_at(tmp_path, _hybrid(), _MIDNIGHT)

    assert hybrid["pv"] == plain["pv"]


@pytest.mark.asyncio
async def test_a_hybrid_with_no_array_nameplate_produces_nothing(tmp_path: Path) -> None:
    """As a clone writes one: a tree does not publish the array's rating."""
    plain = await _flows_at(tmp_path, panel_config(), _NOON)
    hybrid = await _flows_at(tmp_path, _hybrid(nameplate_w=None), _NOON)

    assert hybrid["pv"] == plain["pv"]


def _raw(config: SimulationConfig) -> dict[str, object]:
    """*config* as the YAML a user writes, which validation reads before any type."""
    loaded: dict[str, object] = yaml.safe_load(yaml.safe_dump(dict(config)))
    return loaded


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param({"inverter_type": "dc-coupled"}, r"bess\.inverter_type", id="unknown type"),
        pytest.param(
            {"inverter_type": "ac-coupled", "pv_array": {"nameplate_capacity_w": 5000}},
            r"bess\.pv_array",
            id="an array on an ac-coupled battery",
        ),
        pytest.param(
            {"pv_array": {"nameplate_capacity_w": 5000}},
            r"bess\.pv_array",
            id="an array by default",
        ),
        pytest.param(
            {"inverter_type": "hybrid", "pv_array": {"nameplate_capacity_w": -1}},
            r"bess\.pv_array\.nameplate_capacity_w",
            id="a negative rating",
        ),
        pytest.param(
            {"inverter_type": "hybrid", "pv_array": {"nameplate_capacity_w": "7.6 kW"}},
            r"bess\.pv_array\.nameplate_capacity_w",
            id="a rating that is not a number",
        ),
        pytest.param(
            {"inverter_type": "hybrid", "pv_array": {"nameplate_capacity_w": float("nan")}},
            r"bess\.pv_array\.nameplate_capacity_w",
            id="a rating that is not finite",
        ),
        pytest.param(
            {"inverter_type": "hybrid", "pv_array": 7600},
            r"bess\.pv_array",
            id="an array that is not a section",
        ),
    ],
)
def test_a_battery_s_coupling_it_cannot_read_is_refused(
    change: dict[str, object], message: str
) -> None:
    raw = _raw(panel_config())
    bess = raw["bess"]
    assert isinstance(bess, dict)
    bess.update(change)
    with pytest.raises(ValueError, match=message):
        validate_yaml_config(raw)


@pytest.mark.parametrize("name", reference_capture_names())
def test_a_clone_reads_a_battery_s_coupling_as_the_emitter_s_capture_does(name: str) -> None:
    tree = load_reference_capture(name).tree
    (panel,) = [i for i, d in tree.items() if d.type == "distribution-enclosure"]
    config = translate_panel_tree(panel, discovered_from_tree(tree))
    bess = config.get("bess")
    coupling = bess.get("inverter_type") if isinstance(bess, dict) else None

    assert (coupling == "hybrid") == (name in _HYBRID_CAPTURES), coupling


@pytest.mark.parametrize(
    ("name", "coupling"),
    [
        pytest.param("r202639-f", "hybrid", id="a definition writing it hybrid"),
        pytest.param("r202639-c", None, id="a definition leaving it ac-coupled"),
    ],
)
def test_an_import_keeps_its_definition_s_coupling(name: str, coupling: str | None) -> None:
    """``r202639-c``'s recorded definition leaves its battery ac-coupled, though its tree
    alone would be read hybrid: the definition says what the panel is."""
    config = config_from_definition(load_reference_capture(name).definition)
    bess = config.get("bess")
    assert isinstance(bess, dict)
    assert bess.get("inverter_type") == coupling


@pytest.mark.asyncio
async def test_the_model_view_books_a_hybrid_battery_s_array_as_solar(tmp_path: Path) -> None:
    """Its After pass gives the array to the solar trace by day, and nothing by night."""
    entity = "sensor.synthetic_circuit_power"

    async def after_pv(config: SimulationConfig, where: Path) -> list[float]:
        config["circuits"][0]["recorder_entity"] = entity
        engine = DynamicSimulationEngine(
            config_data=config, recorder=await recorder_history(where, entity)
        )
        await engine.initialize_async()
        data = await engine.compute_modeling_data(24)
        assert "error" not in data, data
        trace: list[float] = data["pv_power_after"]
        return trace

    (tmp_path / "plain").mkdir()
    (tmp_path / "hybrid").mkdir()
    plain = await after_pv(panel_config(), tmp_path / "plain")
    hybrid = await after_pv(_hybrid(), tmp_path / "hybrid")

    assert all(h >= p for h, p in zip(hybrid, plain, strict=True))
    assert any(h > p for h, p in zip(hybrid, plain, strict=True))
    assert any(h == p for h, p in zip(hybrid, plain, strict=True))
