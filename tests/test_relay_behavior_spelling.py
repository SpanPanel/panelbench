"""One spelling for a locked relay wherever a user reads it: `non-controllable`.

The shipped configs write `relay_behavior: non-controllable`, and that is the spelling
the dashboard offers, the validation message asks for and the docs show. A config may
still say `non_controllable`, or `always_on` for `always-on`: `normalise_relay_behavior`
reads every spelling as the same lock, and so must the dashboard. Its edit form used to
select an option only on an exact match, so a circuit spelt the shipped way showed
`controllable` selected, and saving any other field of it unlocked the relay.
"""

from __future__ import annotations

import re

import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard.config_store import ConfigStore
from panelbench.validation import validate_single_template

_CONFIG = """\
panel_config:
  serial_number: sim-relay-spelling
  total_tabs: 8
  main_size: 200
circuit_templates:
  shipped:
    relay_behavior: non-controllable
    priority: NEVER
    breaker_rating: 20
    energy_profile: {mode: consumer, power_range: [0.0, 150.0], typical_power: 80.0}
  underscored:
    relay_behavior: non_controllable
    priority: NEVER
    breaker_rating: 20
    energy_profile: {mode: consumer, power_range: [0.0, 150.0], typical_power: 80.0}
  always:
    relay_behavior: always_on
    priority: NEVER
    breaker_rating: 20
    energy_profile: {mode: consumer, power_range: [0.0, 150.0], typical_power: 80.0}
  backup_system:
    relay_behavior: non_controllable
    priority: NEVER
    commissioned_system: backup
    breaker_rating: 20
    energy_profile: {mode: consumer, power_range: [0.0, 150.0], typical_power: 80.0}
circuits:
- {id: shipped, name: Shipped, template: shipped, tabs: [1]}
- {id: underscored, name: Underscored, template: underscored, tabs: [3]}
- {id: always, name: Always, template: always, tabs: [5]}
- {id: backup_system, name: Commissioned Backup System, template: backup_system, tabs: [7]}
"""

_RELAY_SELECT = re.compile(r'<select name="relay_behavior"[^>]*>(.*?)</select>', re.DOTALL)
_OPTION = re.compile(r'<option value="([^"]*)"\s*(selected)?\s*>([^<]*)</option>')


@pytest.fixture
def app(tmp_path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "panel.yaml").write_text(_CONFIG, encoding="utf-8")
    ctx = DashboardContext(
        config_dir=cfg_dir,
        config_filter="panel.yaml",
        get_panel_configs=lambda: {},
        get_panel_ports=lambda: {},
        request_reload=lambda: None,
    )
    return create_dashboard_app(ctx)


async def _relay_options(client: TestClient, entity_id: str) -> list[tuple[str, bool, str]]:
    """Each relay option on *entity_id*'s edit form: value, selected, label."""
    form = await (await client.get(f"/entities/{entity_id}/edit")).text()
    match = _RELAY_SELECT.search(form)
    assert match is not None, form
    return [(value, bool(selected), label) for value, selected, label in _OPTION.findall(match[1])]


@pytest.mark.parametrize("entity_id", ["shipped", "underscored"])
@pytest.mark.asyncio
async def test_the_edit_form_selects_a_locked_relay_however_it_is_spelt(app, entity_id) -> None:
    async with TestClient(TestServer(app)) as client:
        options = await _relay_options(client, entity_id)

    assert options == [
        ("controllable", False, "controllable"),
        ("non-controllable", True, "non-controllable"),
    ]


@pytest.mark.asyncio
async def test_the_edit_form_keeps_an_always_on_relay_selected(app) -> None:
    """Not offered as a choice, but shown as the current value rather than replaced."""
    async with TestClient(TestServer(app)) as client:
        options = await _relay_options(client, "always")

    assert [(value, selected) for value, selected, _label in options if selected] == [
        ("always-on", True)
    ]


@pytest.mark.asyncio
async def test_a_commissioned_circuit_accepts_its_relay_back_in_either_spelling(app) -> None:
    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/entities/backup_system",
            data={"priority": "NEVER", "relay_behavior": "non-controllable"},
        )

        assert resp.status == 200


def test_the_validation_message_asks_for_the_shipped_spelling() -> None:
    template = {
        "energy_profile": {"mode": "consumer"},
        "relay_behavior": "controllable",
        "priority": "NEVER",
        "commissioned_system": "pv",
    }

    with pytest.raises(ValueError, match=r"set relay_behavior: non-controllable$"):
        validate_single_template("solar", template)


def test_a_pv_circuit_added_from_the_dashboard_is_spelt_as_shipped() -> None:
    store = ConfigStore()
    store.load_from_yaml(_CONFIG)

    added = store.add_entity("pv")

    assert added.relay_behavior == "non-controllable"
