"""A breaker's circuits are one breaker: a config PanelBench accepts is one the panel runs.

Under a variant that publishes shared breakers, a tandem's or quad's circuits sit behind
one meter, one relay and one position, so the emitter refuses circuits on one breaker
that differ in priority, relay lock, never-backup or energy seed, as it refuses a PV
rating that is not above 0. PanelBench validates a config by the emitter's own rules
before anything runs it, naming circuits by their config ids, and the dashboard edits
and toggles a breaker as one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard.config_store import ConfigStore
from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS

if TYPE_CHECKING:
    from pathlib import Path

_TANDEMS: Final = CONFIGS / "ref_MLO_48_TANDEMS.yaml"
_MATE: Final = "circuit_37"
_OTHER_MATE: Final = "circuit_37_2"


def _raw(path: Path) -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _on_its_own_template(config: dict[str, object], circuit_id: str, **fields: object) -> None:
    """Move *circuit_id* onto a copy of its template, with *fields* changed."""
    circuits = config["circuits"]
    templates = config["circuit_templates"]
    assert isinstance(circuits, list)
    assert isinstance(templates, dict)
    (circuit,) = [c for c in circuits if c["id"] == circuit_id]
    own = dict(templates[circuit["template"]])
    own.update(fields)
    templates[f"{circuit_id}_own"] = own
    circuit["template"] = f"{circuit_id}_own"


@pytest.mark.parametrize(
    ("fields", "what"),
    [
        pytest.param({"priority": "OFF_GRID"}, "default-priority", id="priority"),
        pytest.param({"relay_behavior": "non_controllable"}, "lock", id="relay lock"),
    ],
)
def test_circuits_of_one_breaker_that_differ_are_refused_by_name(
    fields: dict[str, object], what: str
) -> None:
    config = _raw(_TANDEMS)
    _on_its_own_template(config, _MATE, **fields)

    with pytest.raises(ValueError, match=what) as refused:
        validate_yaml_config(config)

    assert _MATE in str(refused.value)
    assert _OTHER_MATE in str(refused.value)


def test_a_pv_rating_that_is_not_above_zero_is_refused() -> None:
    config = _raw(CONFIGS / "ref_MAIN_32_TANDEMS.yaml")
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    for template in templates.values():
        profile = template.get("energy_profile") or {}
        if profile.get("mode") == "producer":
            profile["nameplate_capacity_w"] = 0
    with pytest.raises(ValueError, match="nominal-power-w"):
        validate_yaml_config(config)


@pytest.mark.parametrize(
    ("field", "value", "seen"),
    [
        pytest.param("priority", "OFF_GRID", "OFF_GRID", id="priority"),
        pytest.param(
            "relay_behavior", "non_controllable", "non-controllable", id="relay behaviour"
        ),
    ],
)
def test_editing_one_circuit_of_a_breaker_edits_the_breaker(
    field: str, value: str, seen: str
) -> None:
    store = ConfigStore()
    store.load_from_file(_TANDEMS)

    store.update_entity(_MATE, {field: value, "_dirty": "1"})

    for circuit_id in (_MATE, _OTHER_MATE):
        entity = store.get_entity(circuit_id)
        shown = entity.priority if field == "priority" else entity.relay_behavior_option
        assert shown == seen, circuit_id


@pytest.mark.asyncio
async def test_the_dashboard_s_relay_toggle_switches_the_whole_breaker(tmp_path: Path) -> None:
    """A breaker has one relay, so opening one of its circuits opens them all."""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "panel.yaml").write_text(_TANDEMS.read_text(encoding="utf-8"), encoding="utf-8")
    calls: list[tuple[str, str]] = []
    ctx = DashboardContext(
        config_dir=config_dir,
        config_filter="panel.yaml",
        get_panel_configs=lambda: {},
        get_panel_ports=lambda: {},
        request_reload=lambda: None,
        set_circuit_relay=lambda circuit_id, state: calls.append((circuit_id, state)),
    )
    async with TestClient(TestServer(create_dashboard_app(ctx))) as client:
        response = await client.post(f"/entities/{_MATE}/relay", json={"relay_state": "OPEN"})
        assert response.status == 200

    assert sorted(calls) == [(_MATE, "OPEN"), (_OTHER_MATE, "OPEN")]
