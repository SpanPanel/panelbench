"""A circuit on no breaker space is a virtual device: modelled, never published.

The dashboard adds a PV inverter or a SPAN Drive as a circuit with no spaces for a what-if.
The Model view models it, but a panel publishes only the circuits on its spaces, and the
emitter refuses a circuit with none, so it is left out of what the emitter is handed, and
the panel starts and publishes its real circuits alone.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

import pytest
import yaml

from panelbench.config_defaults import normalize_config
from panelbench.dashboard.config_store import ConfigStore
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.definition import build_definition
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import recorded_panel
from panelbench.engine import DynamicSimulationEngine
from panelbench.validation import validate_yaml_config
from tests._helpers import NOON, PANEL_CONFIG, panel_config, write_config

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from panelbench.config_types import SimulationConfig


def _without_pv() -> SimulationConfig:
    """The working MAIN 32 with no PV: no PV circuit and no ``pv`` section."""
    config = panel_config()
    templates = config["circuit_templates"]
    config["circuits"] = [
        c for c in config["circuits"] if templates[c["template"]].get("device_type") != "pv"
    ]
    config.pop("pv", None)
    return config


def _from_the_dashboard(path: Path, add: str | None = None) -> tuple[dict[str, object], str]:
    """The config at *path* as the dashboard exports it, with an *add* added from the
    dashboard where one is given, and the added circuit's id (empty with none)."""
    store = ConfigStore()
    store.load_from_file(path)
    added = store.add_entity(add).id if add is not None else ""
    config: dict[str, object] = yaml.safe_load(store.export_yaml())
    return config, added


async def _published_devices(tmp_path: Path, config: Mapping[str, object]) -> set[str]:
    """Every device the panel publishes a topic for, after one tick."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    await emitter_runtime.publish_tick(runtime)
    return {topic.split("/")[2] for topic in recorder.retained}


def _serial(config: Mapping[str, object]) -> str:
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    return str(panel["serial_number"])


@pytest.mark.parametrize("kind", ["pv", "evse"])
def test_a_virtual_device_loads(kind: str) -> None:
    config, added = _from_the_dashboard(PANEL_CONFIG, kind)
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    (circuit,) = [c for c in circuits if c["id"] == added]
    assert not circuit.get("tabs")

    validate_yaml_config(config)


@pytest.mark.parametrize("kind", ["pv", "evse"])
@pytest.mark.parametrize("base", ["with its PV", "without PV"])
@pytest.mark.asyncio
async def test_a_virtual_device_leaves_what_the_panel_publishes_as_it_was(
    tmp_path: Path, base: str, kind: str
) -> None:
    """The same devices, the panel's own inverter and SPAN Drive among them where it has
    them: no device for the what-if one, none fed by it, none it displaces."""
    source = _without_pv() if base == "without PV" else panel_config()
    path = write_config(tmp_path / "source.yaml", source)
    before, _ = _from_the_dashboard(path)
    after, added = _from_the_dashboard(path, kind)

    published = await _published_devices(tmp_path / "after", after)

    assert published == await _published_devices(tmp_path / "before", before)
    assert stable_circuit_uuid(_serial(after), added) not in published


@pytest.mark.parametrize("kind", ["pv", "evse"])
@pytest.mark.asyncio
async def test_a_virtual_device_s_power_reaches_no_tick(kind: str) -> None:
    """The emitter books every circuit a tick carries into the panel's flows and its
    battery's dispatch, so a what-if device's power must reach none of them."""
    config, added = _from_the_dashboard(PANEL_CONFIG, kind)
    engine = DynamicSimulationEngine(config_data=config)
    await engine.initialize_async()

    tick = await engine.get_tick_inputs()

    assert stable_circuit_uuid(_serial(config), added) not in tick.circuits
    assert stable_circuit_uuid(_serial(config), "solar_inverter") in tick.circuits


@pytest.mark.parametrize(
    ("section", "named"),
    [pytest.param("pv", r"pv\.feed", id="pv"), pytest.param("bess", r"bess\.feed", id="bess")],
)
def test_no_feed_names_a_virtual_device(section: str, named: str) -> None:
    """A what-if device feeds no inverter or battery the panel publishes."""
    config, added = _from_the_dashboard(PANEL_CONFIG, "pv")
    fed = config[section]
    assert isinstance(fed, dict)
    fed["feed"] = added

    with pytest.raises(ValueError, match=rf"{named} names circuit '{added}', which sits on no"):
        validate_yaml_config(config)


def _pv_devices(config: dict[str, object]) -> list[dict[str, str]]:
    """The metadata of each PV device the panel's definition holds."""
    normalised = cast("SimulationConfig", copy.deepcopy(config))
    normalize_config(normalised, source="test")
    return [
        dict(device.metadata) for device in build_definition(normalised).manifest.of_class("pv")
    ]


def test_a_virtual_pv_lends_the_panel_s_own_inverter_nothing(tmp_path: Path) -> None:
    """With no PV circuit, the inverter the ``pv`` section describes takes its type from
    the panel's first producer circuit, and a what-if PV is none of the panel's."""
    source = _without_pv()
    source["pv"] = {"enabled": True, "nameplate_capacity_w": 6000.0}
    path = write_config(tmp_path / "source.yaml", source)
    before, _ = _from_the_dashboard(path)
    store = ConfigStore()
    store.load_from_file(path)
    added = store.add_entity("pv").id
    store.update_entity(added, {"inverter_type": "hybrid"})
    after: dict[str, object] = yaml.safe_load(store.export_yaml())

    assert _pv_devices(after) == _pv_devices(before)
    assert len(_pv_devices(before)) == 1


@pytest.mark.asyncio
async def test_the_model_view_models_a_virtual_pv() -> None:
    config, added = _from_the_dashboard(PANEL_CONFIG, "pv")
    engine = DynamicSimulationEngine(config_data=config)
    await engine.initialize_async()

    assert engine.modelled_circuit_power(added, NOON) > 0
