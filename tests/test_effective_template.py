"""A circuit is modelled, published and checked by one template: its own, as it has it.

A circuit's ``overrides`` and own ``breaker_rating`` sit over its template. The engine
models the circuit by what that leaves, so the definition publishes it by the same, and
validation checks the same (``circuit.effective_template``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
import yaml

from panelbench.dashboard.config_store import ConfigStore
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import recorded_panel
from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS, PANEL_CONFIG, published, write_config

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_MLO_48: Final = CONFIGS / "ref_MLO_48_TANDEMS.yaml"


def _raw(path: Path) -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _circuit(config: Mapping[str, object], circuit_id: str) -> dict[str, object]:
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    (circuit,) = [c for c in circuits if c["id"] == circuit_id]
    assert isinstance(circuit, dict)
    return circuit


def _template(config: Mapping[str, object], circuit_id: str) -> dict[str, object]:
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    template = templates[_circuit(config, circuit_id)["template"]]
    assert isinstance(template, dict)
    return template


async def _published_rating(tmp_path: Path, config: Mapping[str, object], circuit_id: str) -> str:
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    await emitter_runtime.publish_tick(runtime)
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    uuid = stable_circuit_uuid(str(panel["serial_number"]), circuit_id)
    return published(recorder, uuid, "breaker/rating")


@pytest.mark.parametrize(
    ("source", "circuit_id"),
    [
        pytest.param(PANEL_CONFIG, "kitchen_lights", id="MAIN 32"),
        pytest.param(_MLO_48, "circuit_1", id="MLO 48"),
    ],
)
@pytest.mark.asyncio
async def test_a_breaker_rating_edited_in_the_dashboard_is_published(
    tmp_path: Path, source: Path, circuit_id: str
) -> None:
    """The edit writes the circuit's own rating, which the panel publishes, where it
    went on publishing its template's."""
    store = ConfigStore()
    store.load_from_file(source)
    store.update_entity(circuit_id, {"breaker_rating": "45", "_dirty": "1"})
    config: dict[str, object] = yaml.safe_load(store.export_yaml())

    assert await _published_rating(tmp_path, config, circuit_id) == "45"


@pytest.mark.asyncio
async def test_a_circuit_s_own_rating_replaces_its_template_s_legacy_one(tmp_path: Path) -> None:
    config = _raw(PANEL_CONFIG)
    _template(config, "kitchen_lights")["breaker_rating_a"] = 15
    _circuit(config, "kitchen_lights")["breaker_rating"] = 30

    assert await _published_rating(tmp_path, config, "kitchen_lights") == "30"


def test_a_template_rating_every_circuit_on_it_replaces_is_read_by_nothing() -> None:
    config = _raw(PANEL_CONFIG)
    _template(config, "kitchen_lights")["breaker_rating"] = [1, 2]
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    sharing = [
        c for c in circuits if c["template"] == _circuit(config, "kitchen_lights")["template"]
    ]
    for circuit in sharing:
        circuit["breaker_rating"] = 20

    validate_yaml_config(config)


@pytest.mark.parametrize(
    ("at", "reason"),
    [
        pytest.param(
            "template",
            r"Circuit template '[^']+'(, as circuit \d+ has it,)? breaker_rating must be",
            id="template",
        ),
        pytest.param(
            "circuit",
            r"Circuit template '[^']+', as circuit \d+ has it, breaker_rating must be",
            id="the circuit's own",
        ),
    ],
)
def test_a_rating_the_panel_would_publish_is_checked_where_it_is_written(
    at: str, reason: str
) -> None:
    config = _raw(PANEL_CONFIG)
    target = (
        _template(config, "kitchen_lights")
        if at == "template"
        else _circuit(config, "kitchen_lights")
    )
    target["breaker_rating"] = "abc"

    with pytest.raises(ValueError, match=reason):
        validate_yaml_config(config)
