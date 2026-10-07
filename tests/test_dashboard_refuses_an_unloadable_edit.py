"""The dashboard refuses an edit the panel would not load, and nothing else.

Every change to the editor's config is made on a copy, normalised and validated as
the panel will validate it, and swapped in only if the panel would load it.
Otherwise the request answers 422 with the reason, the editor's config is as it
was, and the page reloads, so it never shows an edit the editor does not hold.

Refusing the save instead, as the dashboard did, threw away every unsaved edit made
before the one the panel refused, and cleared the flag that warns before switching
panels.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard import config_store as config_store_module
from panelbench.dashboard.keys import APP_KEY_STORE
from tests._helpers import default_config, write_config

_FILE = "panel.yaml"


@pytest.fixture
def app(tmp_path: Path) -> web.Application:
    """The dashboard, editing a saved copy of the shipped MAIN 40 config."""
    write_config(tmp_path / _FILE, default_config())
    return create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter=_FILE,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )


def _refuse_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the panel refuse any config the editor validates from here on."""

    def refuse(_config: object) -> None:
        raise ValueError("refused for the test")

    monkeypatch.setattr(config_store_module, "validate_yaml_config", refuse)


@pytest.mark.parametrize(
    ("method", "path", "form"),
    [
        ("PUT", "/panel-config", {"serial_number": "renamed"}),
        ("PUT", "/sim-params", {"update_interval": "7"}),
        ("POST", "/entities", {"entity_type": "evse"}),
        ("DELETE", "/entities/living_room_lights", {}),
        ("PUT", "/entities/pool_pump/profile", {"hour_0": "0.5"}),
        ("POST", "/entities/pool_pump/profile/preset", {"preset": "always_on"}),
        ("PUT", "/entities/pool_pump/active-days", {"days_submitted": "1", "day_0": "on"}),
        (
            "PUT",
            "/entities/span_drive_garage/evse-schedule",
            {"charge_start": "1", "charge_duration": "4"},
        ),
        ("POST", "/entities/span_drive_garage/evse-schedule/preset", {"preset": "night"}),
    ],
    ids=[
        "panel config",
        "sim params",
        "add circuit",
        "delete circuit",
        "profile",
        "preset",
        "active days",
        "EV schedule",
        "EV preset",
    ],
)
@pytest.mark.asyncio
async def test_every_kind_of_edit_is_refused_whole(
    app: web.Application,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    form: dict[str, str],
) -> None:
    store = app[APP_KEY_STORE]
    before = store.export_yaml()
    _refuse_everything(monkeypatch)

    async with TestClient(TestServer(app)) as client:
        refused = await client.request(method, path, data=form)
        reason = await refused.text()

    assert refused.status == 422
    assert "refused for the test" in reason
    assert refused.headers.get("HX-Refresh") == "true", "the page reloads to what the editor holds"
    assert store.export_yaml() == before
    assert not store.dirty


@pytest.mark.asyncio
async def test_an_earlier_unsaved_edit_survives_a_later_refused_one(
    app: web.Application,
) -> None:
    """Tabs 1 and 4 are not a double-pole pair, so the panel refuses that edit alone."""
    async with TestClient(TestServer(app)) as client:
        renamed = await client.put("/panel-config", data={"serial_number": "sim-renamed"})
        refused = await client.put("/entities/bedroom_lights", data={"tabs": "1,4"})
        reason = await refused.text()
        dirty = await (await client.get("/check-dirty")).json()
        page = await (await client.get("/")).text()

    store = app[APP_KEY_STORE]
    assert renamed.status == 200
    assert refused.status == 422
    assert "mixed parity" in reason
    assert store.get_panel_config()["serial_number"] == "sim-renamed"
    assert store.get_entity("bedroom_lights").tabs != [1, 4]
    assert dirty == {"dirty": True}, "the rename is still unsaved, so switching panels still asks"
    assert "mixed parity" in page, "the reloaded page says why"


@pytest.mark.asyncio
async def test_a_refused_edit_leaves_a_saved_config_saved(app: web.Application) -> None:
    async with TestClient(TestServer(app)) as client:
        refused = await client.put("/entities/bedroom_lights", data={"tabs": "1,4"})
        dirty = await (await client.get("/check-dirty")).json()

    assert refused.status == 422
    assert dirty == {"dirty": False}


@pytest.mark.asyncio
async def test_a_new_circuit_takes_the_first_free_space(app: web.Application) -> None:
    """A circuit with no space is one the panel refuses, so it is placed on one."""
    store = app[APP_KEY_STORE]
    free = store.get_unmapped_tabs()

    async with TestClient(TestServer(app)) as client:
        added = await client.post("/entities", data={"entity_type": "circuit"})

    assert added.status == 200
    assert free[0] not in store.get_unmapped_tabs()
