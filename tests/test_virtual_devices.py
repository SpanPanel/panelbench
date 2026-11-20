"""A circuit on no breaker space is a virtual device: modelled, never published.

The dashboard adds a PV inverter or a SPAN Drive as a circuit with no spaces for a what-if.
The Model view models it, but a panel publishes only the circuits on its spaces, and the
emitter refuses a circuit with none, so it is left out of what the emitter is handed, and
the panel starts and publishes its real circuits alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from panelbench.dashboard.config_store import ConfigStore
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import recorded_panel
from panelbench.engine import DynamicSimulationEngine
from panelbench.validation import validate_yaml_config
from tests._helpers import NOON, PANEL_CONFIG

if TYPE_CHECKING:
    from pathlib import Path


def _with_virtual(kind: str) -> tuple[dict[str, object], str]:
    """The working panel with a *kind* added from the dashboard, and the added id."""
    store = ConfigStore()
    store.load_from_file(PANEL_CONFIG)
    added = store.add_entity(kind)
    config: dict[str, object] = yaml.safe_load(store.export_yaml())
    return config, added.id


@pytest.mark.parametrize("kind", ["pv", "evse"])
def test_a_virtual_device_loads(kind: str) -> None:
    config, added = _with_virtual(kind)
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    (circuit,) = [c for c in circuits if c["id"] == added]
    assert not circuit.get("tabs")

    validate_yaml_config(config)


@pytest.mark.parametrize("kind", ["pv", "evse"])
@pytest.mark.asyncio
async def test_a_virtual_device_is_not_published(tmp_path: Path, kind: str) -> None:
    config, added = _with_virtual(kind)
    path = tmp_path / "panel.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    runtime, recorder = await recorded_panel(path)
    await emitter_runtime.publish_tick(runtime)

    panel_config = config["panel_config"]
    assert isinstance(panel_config, dict)
    uuid = stable_circuit_uuid(str(panel_config["serial_number"]), added)
    assert not [topic for topic in recorder.retained if f"/{uuid}/" in topic]


@pytest.mark.asyncio
async def test_the_model_view_models_a_virtual_pv() -> None:
    config, added = _with_virtual("pv")
    engine = DynamicSimulationEngine(config_data=config)
    await engine.initialize_async()

    assert engine.modelled_circuit_power(added, NOON) > 0
