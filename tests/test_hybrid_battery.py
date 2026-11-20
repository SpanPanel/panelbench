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
from panelbench.engine import DynamicSimulationEngine, RealisticBehaviorEngine
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


async def _flows_at(
    tmp_path: Path, config: SimulationConfig, at: str, *, grid_online: bool = True
) -> dict[str, float]:
    """The panel's four power flows after one tick at simulated *at*."""
    timed = copy.deepcopy(config)
    timed["simulation_params"]["use_simulation_time"] = True
    timed["simulation_params"]["simulation_start_time"] = at
    # The circuits' noise is drawn from `random`, and the array draws none, so two runs
    # compared with each other draw the same.
    random.seed(20261119)
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime, recorder = await recorded_panel(
        write_config(tmp_path / "panel.yaml", timed), grid_online=grid_online
    )
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


_ENTITY: Final = "sensor.synthetic_circuit_power"


async def _modeling(config: SimulationConfig, where: Path) -> dict[str, list[float]]:
    """The Model view's series for *config* over a recorder history kept in *where*."""
    where.mkdir()
    config["circuits"][0]["recorder_entity"] = _ENTITY
    engine = DynamicSimulationEngine(
        config_data=config, recorder=await recorder_history(where, _ENTITY)
    )
    await engine.initialize_async()
    data = await engine.compute_modeling_data(24)
    assert "error" not in data, data
    series: dict[str, list[float]] = {
        key: [float(value) for value in data[key]]
        for key in (
            "timestamps",
            "site_power",
            "grid_power",
            "pv_power_before",
            "pv_power_after",
            "battery_power",
            "battery_power_before",
        )
    }
    return series


@pytest.mark.asyncio
async def test_the_model_view_books_a_hybrid_battery_s_array_as_the_emitter_does(
    tmp_path: Path,
) -> None:
    """In both passes: the array's own production joins the solar, the battery's
    dispatch sees it, and the grid, battery and solar still add up to the panel's load."""
    plain = await _modeling(panel_config(), tmp_path / "plain")
    hybrid = await _modeling(_hybrid(), tmp_path / "hybrid")
    sun = RealisticBehaviorEngine(_hybrid())
    array = [round(sun.solar_output_w(_ARRAY_W, ts), 1) for ts in hybrid["timestamps"]]
    assert any(array) and not all(array)

    for before, after in (("pv_power_before", "pv_power_after"),):
        assert [
            round(h - p, 1) for h, p in zip(hybrid[before], plain[before], strict=True)
        ] == pytest.approx(array, abs=0.2)
        assert [
            round(h - p, 1) for h, p in zip(hybrid[after], plain[after], strict=True)
        ] == pytest.approx(array, abs=0.2)
    for grid, battery, pv in (
        ("site_power", "battery_power_before", "pv_power_before"),
        ("grid_power", "battery_power", "pv_power_after"),
    ):
        loads = [
            [g + b + s for g, b, s in zip(run[grid], run[battery], run[pv], strict=True)]
            for run in (plain, hybrid)
        ]
        assert loads[1] == pytest.approx(loads[0], abs=0.5), grid
    assert any(h < p for h, p in zip(hybrid["battery_power"], plain["battery_power"], strict=True))


@pytest.mark.asyncio
async def test_a_disabled_hybrid_battery_s_array_produces_nothing(tmp_path: Path) -> None:
    off = panel_config()
    off["bess"]["enabled"] = False
    off_hybrid = _hybrid()
    off_hybrid["bess"]["enabled"] = False

    plain = await _modeling(off, tmp_path / "plain")
    hybrid = await _modeling(off_hybrid, tmp_path / "hybrid")

    assert hybrid["pv_power_after"] == plain["pv_power_after"]


def _cloned(config: SimulationConfig, original_bess: object) -> SimulationConfig:
    """*config* as a clone keeps it, its battery as cloned the Before pass's."""
    config["panel_source"] = {
        "origin_serial": "nt-0000-panel",
        "host": "192.0.2.10",
        "original_bess": original_bess,
    }
    return config


@pytest.mark.asyncio
async def test_rating_a_cloned_hybrid_s_array_is_no_saving(tmp_path: Path) -> None:
    """A clone writes a hybrid battery with no array rating, since a panel publishes
    none, and keeps that battery as the Before pass's. Rating the array afterwards fills
    in what the panel has, so the Before pass books the array as the After pass does."""
    plain = panel_config()
    plain = _cloned(plain, copy.deepcopy(plain["bess"]))
    rated = _hybrid()
    unrated = copy.deepcopy(rated["bess"])
    del unrated["pv_array"]
    rated = _cloned(rated, unrated)

    before_plain = (await _modeling(plain, tmp_path / "plain"))["pv_power_before"]
    modeled = await _modeling(rated, tmp_path / "rated")
    sun = RealisticBehaviorEngine(_hybrid())
    array = [round(sun.solar_output_w(_ARRAY_W, ts), 1) for ts in modeled["timestamps"]]

    added = [
        round(r - p, 1) for r, p in zip(modeled["pv_power_before"], before_plain, strict=True)
    ]
    assert added == pytest.approx(array, abs=0.2)


@pytest.mark.asyncio
async def test_a_battery_turned_hybrid_while_running_restarts_rather_than_changes(
    tmp_path: Path,
) -> None:
    """The emitter's battery is built at start, and the emitter refuses PV for a battery
    it was not started hybrid. So a live change of coupling is not applied, the caller
    restarts the panel on its saved config instead, and ticks go on as they were."""
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", panel_config()))
    del recorder
    turned = copy.deepcopy(_hybrid()["bess"])

    assert emitter_runtime.update_bess_config_live(runtime, turned) is False
    assert runtime.engine.config["bess"].get("inverter_type") != "hybrid"
    await emitter_runtime.publish_tick(runtime)

    kept = copy.deepcopy(runtime.engine.config["bess"])
    kept["backup_reserve_pct"] = 35.0
    assert emitter_runtime.update_bess_config_live(runtime, kept) is True
