"""Cloning, syncing and restoring from a real panel, through the dashboard.

What reaches the panel is counted, and what reaches the config is read back from
disk: the passphrase must reach neither the clone's file nor anything exported
from it, and a restore that cannot reach the panel says so rather than doing
nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.history_generator import SyntheticHistoryGenerator
from panelbench.panel_secrets import PanelSecretsStore
from panelbench.scraper import registered_client_name
from tests._fake_span_panel import FakeSpanPanel, install_fake_scrape, running
from tests._helpers import CAPTURED_MAIN_32_SERIAL, default_config, write_config

_HTMX = {"HX-Request": "true"}
_PASSPHRASE = "example-passphrase-7731"
_CLONE = f"{CAPTURED_MAIN_32_SERIAL}-clone.yaml"


class FakeHomeAssistant:
    """Enough of the HA client for a clone: the home's location, in Denver."""

    async def async_get_home_location(self) -> tuple[float, float]:
        return (39.74, -104.99)


def _dashboard(config_dir: Path, *, config_filter: str | None = None) -> web.Application:
    return create_dashboard_app(
        DashboardContext(
            config_dir=config_dir,
            config_filter=config_filter,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
            ha_client=FakeHomeAssistant(),
        )
    )


@pytest.fixture(autouse=True)
def _no_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """History generation fetches weather; nothing here depends on it."""

    async def generate(_self: SyntheticHistoryGenerator, path: Path) -> Path:
        return path

    monkeypatch.setattr(SyntheticHistoryGenerator, "generate", generate)


@pytest.mark.asyncio
async def test_a_clone_keeps_the_passphrase_out_of_its_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_scrape(monkeypatch)
    async with (
        running(FakeSpanPanel()) as panel,
        TestClient(TestServer(_dashboard(tmp_path))) as client,
    ):
        cloned = await client.post(
            "/clone-from-panel",
            data={"host": panel.host, "passphrase": _PASSPHRASE},
            headers=_HTMX,
        )
        exported = await (await client.get("/export")).text()

    assert cloned.status == 200, await cloned.text()
    written = (tmp_path / _CLONE).read_text(encoding="utf-8")
    assert _PASSPHRASE not in written
    assert "passphrase" not in yaml.safe_load(written)["panel_source"]
    assert _PASSPHRASE not in exported
    assert PanelSecretsStore.in_config_dir(tmp_path).get(panel.serial).passphrase == _PASSPHRASE


@pytest.mark.asyncio
async def test_a_clone_keeps_the_panels_time_zone_over_home_assistants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home Assistant's home places the clone; the panel's own time zone stays."""
    install_fake_scrape(monkeypatch)
    async with (
        running(FakeSpanPanel()) as panel,
        TestClient(TestServer(_dashboard(tmp_path))) as client,
    ):
        await client.post(
            "/clone-from-panel",
            data={"host": panel.host, "passphrase": _PASSPHRASE},
            headers=_HTMX,
        )

    panel_config = yaml.safe_load((tmp_path / _CLONE).read_text(encoding="utf-8"))["panel_config"]
    assert (panel_config["latitude"], panel_config["longitude"]) == (39.74, -104.99)
    assert panel_config["time_zone"] == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_a_sync_reuses_the_clones_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scrape = install_fake_scrape(monkeypatch)
    async with running(FakeSpanPanel()) as panel:
        async with TestClient(TestServer(_dashboard(tmp_path))) as client:
            await client.post(
                "/clone-from-panel",
                data={"host": panel.host, "passphrase": _PASSPHRASE},
                headers=_HTMX,
            )
        # A later session, editing the clone it wrote.
        dashboard = _dashboard(tmp_path, config_filter=_CLONE)
        async with TestClient(TestServer(dashboard)) as client:
            synced = await client.post("/sync-panel-source", headers=_HTMX)
            html = await synced.text()

    assert "Sync failed" not in html, html
    assert [r["name"] for r in panel.registrations] == [registered_client_name(panel.serial)]
    assert len(scrape.attempts) == 2


@pytest.mark.asyncio
async def test_a_clone_from_existing_broker_credentials_registers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scrape = install_fake_scrape(monkeypatch)
    async with (
        running(FakeSpanPanel()) as panel,
        TestClient(TestServer(_dashboard(tmp_path))) as client,
    ):
        cloned = await client.post(
            "/clone-from-panel",
            data={
                "host": panel.host,
                "broker_serial": panel.serial,
                "broker_username": "existing-user",
                "broker_password": "existing-broker-password",
                "broker_port": "8883",
                "broker_ca": "",
            },
            headers=_HTMX,
        )

    assert cloned.status == 200, await cloned.text()
    assert (tmp_path / _CLONE).exists(), await cloned.text()
    assert panel.registrations == []
    assert scrape.users == ["existing-user"]
    assert "existing-broker-password" not in (tmp_path / _CLONE).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_existing_broker_credentials_need_all_three_fields(tmp_path: Path) -> None:
    async with TestClient(TestServer(_dashboard(tmp_path))) as client:
        answered = await client.post(
            "/clone-from-panel",
            data={"host": "192.0.2.10", "broker_username": "existing-user"},
            headers=_HTMX,
        )
        html = await answered.text()

    assert "missing: serial, password" in html
    assert not list(tmp_path.glob("*.yaml"))


@pytest.mark.asyncio
async def test_the_clone_form_asks_for_the_passphrase_in_a_password_field(tmp_path: Path) -> None:
    async with TestClient(TestServer(_dashboard(tmp_path))) as client:
        html = await (await client.get("/clone-panel-section")).text()

    [field] = re.findall(r'<input[^>]*name="passphrase"[^>]*>', html)
    assert 'type="password"' in field
    assert "value=" not in field, "a passphrase is never sent back to the browser"


@pytest.mark.asyncio
async def test_an_imported_config_drops_its_passphrase_from_everything_written(
    tmp_path: Path,
) -> None:
    """A clone written before the store existed, imported: the passphrase moves to
    the store, and neither an export nor a save carries it again."""
    config = default_config()
    config["panel_source"] = {
        "origin_serial": "example-panel-001",
        "host": "192.0.2.10",
        "passphrase": _PASSPHRASE,
    }
    upload = FormData()
    upload.add_field(
        "file", yaml.safe_dump(dict(config), sort_keys=False).encode(), filename="legacy.yaml"
    )
    async with TestClient(TestServer(_dashboard(tmp_path))) as client:
        imported = await client.post("/import", data=upload, headers=_HTMX)
        exported = await (await client.get("/export")).text()

    assert imported.status == 200, await imported.text()
    assert _PASSPHRASE not in exported
    assert PanelSecretsStore.in_config_dir(tmp_path).get("example-panel-001").passphrase == (
        _PASSPHRASE
    )


@pytest.mark.asyncio
async def test_a_restore_that_cannot_reach_the_panel_says_so(tmp_path: Path) -> None:
    """It used to swallow the failure and leave the circuit as it was, silently."""
    config = default_config()
    # Nothing listens on port 9 of the loopback address, so the panel is unreachable.
    config["panel_source"] = {"origin_serial": "example-panel-001", "host": "127.0.0.1:9"}
    write_config(tmp_path / "panel.yaml", config)
    dashboard = _dashboard(tmp_path, config_filter="panel.yaml")
    async with TestClient(TestServer(dashboard)) as client:
        restored = await client.post(
            "/entities/living_room_lights/restore-recorder", headers=_HTMX
        )
        html = await restored.text()

    assert "Restore failed" in html
    assert "[registering]" in html
