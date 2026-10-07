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

import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panelbench import scraper
from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard import config_store as config_store_module
from panelbench.dashboard.config_store import ConfigStore
from panelbench.dashboard.keys import (
    APP_KEY_DASHBOARD_CONTEXT,
    APP_KEY_RATE_CACHE,
    APP_KEY_STORE,
)
from tests._helpers import default_config, write_config

_FILE = "panel.yaml"
# What htmx sends with every request it makes, which is how the dashboard's forms
# reach these routes.
_HTMX = {"HX-Request": "true"}


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
        ("POST", "/entities", {"entity_type": "circuit"}),
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
        ("POST", "/bess", {}),
        ("DELETE", "/bess", {}),
        ("PUT", "/bess", {"nameplate_capacity_kwh": "20"}),
        ("PUT", "/bess/schedule", {"hour_0": "charge"}),
        ("POST", "/bess/schedule/preset", {"preset": "custom"}),
        ("PUT", "/bess/charge-mode", {"charge_mode": "backup-only"}),
        ("PUT", "/bess/active-days", {"days_submitted": "1", "day_0": "on"}),
    ],
    ids=[
        "panel config",
        "sim params",
        "add circuit",
        "add EV charger",
        "delete circuit",
        "profile",
        "preset",
        "active days",
        "EV schedule",
        "EV preset",
        "add battery",
        "remove battery",
        "battery settings",
        "battery schedule",
        "battery preset",
        "battery charge mode",
        "battery active days",
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
        refused = await client.request(method, path, data=form, headers=_HTMX)
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
        refused = await client.put("/entities/bedroom_lights", data={"tabs": "1,4"}, headers=_HTMX)
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


@pytest.mark.parametrize(
    ("method", "path", "form", "reason"),
    [
        ("PUT", "/panel-config", {"total_tabs": "41"}, "even number"),
        ("PUT", "/entities/pool_pump", {"typical_power": "lots"}, "could not convert"),
    ],
    ids=["odd tabs", "non-numeric field"],
)
@pytest.mark.asyncio
async def test_an_edit_its_own_input_refuses_answers_422_with_the_reason(
    app: web.Application, method: str, path: str, form: dict[str, str], reason: str
) -> None:
    store = app[APP_KEY_STORE]
    before = store.export_yaml()

    async with TestClient(TestServer(app)) as client:
        refused = await client.request(method, path, data=form, headers=_HTMX)
        text = await refused.text()

    assert refused.status == 422
    assert reason in text
    assert store.export_yaml() == before


def test_every_public_mutator_is_one_gated_edit() -> None:
    """Structural, so a mutator added later cannot skip the gate: every public method
    that is not a reader carries the gate's wrapper."""
    readers = ("get_", "list_", "has_", "load_from_", "compute_")
    exempt = {"edit", "export_yaml", "save_to_file", "dirty"}
    public = {
        name: member
        for name, member in vars(ConfigStore).items()
        if not name.startswith("_") and callable(member) and name not in exempt
    }
    ungated = sorted(
        name
        for name, member in public.items()
        if not name.startswith(readers) and not hasattr(member, "__wrapped__")
    )

    assert ungated == []
    assert len(public) > 20, "the scan found the store's methods"


@pytest.mark.asyncio
async def test_a_battery_schedule_and_its_days_are_one_edit(
    app: web.Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schedule form saves the hours and the days together, so a refusal of the
    days leaves the hours as they were too."""
    store = app[APP_KEY_STORE]
    before = store.export_yaml()
    real = config_store_module.validate_yaml_config

    def refuse_the_days(config: dict[str, object]) -> None:
        bess = config.get("bess")
        if isinstance(bess, dict) and bess.get("active_days") == [0]:
            raise ValueError("refused for the test")
        real(config)

    monkeypatch.setattr(config_store_module, "validate_yaml_config", refuse_the_days)
    async with TestClient(TestServer(app)) as client:
        refused = await client.put(
            "/bess/schedule",
            data={"hour_0": "charge", "days_submitted": "1", "day_0": "on"},
            headers=_HTMX,
        )

    assert refused.status == 422
    assert store.export_yaml() == before


def test_nothing_outside_the_store_writes_its_state() -> None:
    """The gate holds only if every write goes through the store's own methods, so no
    other module reaches into a store's private state, whatever it calls the store."""
    package = Path(config_store_module.__file__).resolve().parents[1]
    reaches = [
        f"{path.relative_to(package)}:{number}"
        for path in sorted(package.rglob("*.py"))
        if path.name != "config_store.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.search(r"\._(state|dirty)\b", line)
    ]

    assert reaches == []


def _custom_battery_app(tmp_path: Path) -> web.Application:
    """The dashboard, editing a panel whose battery follows a utility rate."""
    config = default_config()
    bess = config.get("bess")
    assert bess is not None
    bess["charge_mode"] = "custom"
    write_config(tmp_path / _FILE, config)
    return create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter=_FILE,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )


@pytest.mark.asyncio
async def test_a_refused_rate_choice_keeps_the_current_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rate dialog calls this with fetch, not htmx, so the refusal comes back in
    the response for the dialog to show, and nothing else changes: not the battery's
    rate, not the simulator-wide current rate, and no notice left for a later page."""
    app = _custom_battery_app(tmp_path)
    store = app[APP_KEY_STORE]
    rates = app[APP_KEY_RATE_CACHE]
    before = store.export_yaml()
    _refuse_everything(monkeypatch)

    async with TestClient(TestServer(app)) as client:
        refused = await client.put("rates/current", json={"label": "rate-b"})
        reason = await refused.text()

    assert refused.status == 422
    assert "refused for the test" in reason
    assert "HX-Refresh" not in refused.headers
    assert store.export_yaml() == before
    assert rates.get_current_rate_label() is None
    assert app[APP_KEY_DASHBOARD_CONTEXT].notice is None


@pytest.mark.asyncio
async def test_a_rate_choice_reaches_the_battery_and_the_current_rate(tmp_path: Path) -> None:
    app = _custom_battery_app(tmp_path)

    async with TestClient(TestServer(app)) as client:
        chosen = await client.put("rates/current", json={"label": "rate-b"})

    assert chosen.status == 200
    assert app[APP_KEY_RATE_CACHE].get_current_rate_label() == "rate-b"
    assert app[APP_KEY_STORE].get_bess_config().get("rate_label") == "rate-b"


@pytest.mark.asyncio
async def test_a_panel_source_sync_says_the_change_is_unsaved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sync refreshes the energy seeds as an unsaved edit, so it says to save."""
    config = default_config()
    config["panel_source"] = {"origin_serial": "nt-0000-tg1", "host": "192.168.1.100"}
    write_config(tmp_path / _FILE, config)
    app = create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter=_FILE,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )

    async def registered(_host: str, _passphrase: str | None) -> tuple[object, str]:
        return object(), ""

    async def scraped(_creds: object, _ca_pem: str) -> object:
        return object()

    monkeypatch.setattr(scraper, "register_with_panel", registered)
    monkeypatch.setattr(scraper, "scrape_ebus", scraped)
    monkeypatch.setattr(app[APP_KEY_STORE], "update_from_scrape", lambda _scraped: True)
    async with TestClient(TestServer(app)) as client:
        synced = await client.post("/sync-panel-source", headers=_HTMX)
        html = await synced.text()

    assert synced.status == 200
    assert "Save to keep it" in html
