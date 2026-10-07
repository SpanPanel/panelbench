"""The dashboard never saves a config nothing will load, and starts when one is active.

A dashboard action used to be able to persist a config the loader refuses: deleting
the circuit `pv.feed` names left the feed dangling, the save went through, and the
panel restart then refused the file. With that config active, an add-on restart
crashed while creating the dashboard, so the user had no screen to fix it from.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard.keys import APP_KEY_DASHBOARD_CONTEXT
from panelbench.validation import validate_yaml_config
from tests._helpers import CURRENT_FIRMWARE, EARLIER_FIRMWARE, default_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.config_types import SimulationConfig

_FILE = "panel.yaml"


def _with_inverters(firmware: str, *extra: str, feed: str | None) -> SimulationConfig:
    """The default panel at *firmware*, with a PV circuit for each of *extra* beside
    its own, and `pv.feed` set to *feed*."""
    config = default_config()
    config["firmware_version"] = firmware
    templates = config["circuit_templates"]
    free = list(config["unmapped_tabs"])
    for circuit_id in extra:
        templates[circuit_id] = copy.deepcopy(templates["solar"])
        config["circuits"].append(
            {"id": circuit_id, "name": circuit_id, "template": circuit_id, "tabs": [free.pop(0)]}
        )
    config["unmapped_tabs"] = free
    pv = config.get("pv")
    assert pv is not None
    if feed is None:
        pv.pop("feed", None)
    else:
        pv["feed"] = feed
    return config


def _app(tmp_path: Path, config: SimulationConfig | dict[str, object]) -> web.Application:
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


def _saved(tmp_path: Path) -> dict[str, object]:
    saved = yaml.safe_load((tmp_path / _FILE).read_text(encoding="utf-8"))
    assert isinstance(saved, dict)
    return saved


@pytest.mark.asyncio
async def test_deleting_the_circuit_pv_feed_names_drops_the_feed(tmp_path: Path) -> None:
    app = _app(tmp_path, _with_inverters(CURRENT_FIRMWARE, "solar_garage", feed="solar_inverter"))

    async with TestClient(TestServer(app)) as client:
        deleted = await client.delete("/entities/solar_inverter")
        saved = await client.post("/save-reload")

    assert deleted.status == 200
    assert saved.status == 200
    config = _saved(tmp_path)
    pv = config["pv"]
    assert isinstance(pv, dict)
    assert "feed" not in pv
    validate_yaml_config(config)


@pytest.mark.asyncio
async def test_a_delete_that_leaves_an_unloadable_config_is_refused(tmp_path: Path) -> None:
    """Before 202639, two PV circuits and no `pv.feed` is refused, so deleting the
    circuit the feed names, out of three, is refused with the reason."""
    config = _with_inverters(EARLIER_FIRMWARE, "solar_garage", "solar_barn", feed="solar_inverter")
    app = _app(tmp_path, config)

    async with TestClient(TestServer(app)) as client:
        deleted = await client.delete("/entities/solar_inverter")
        reason = await deleted.text()
        listed = await (await client.get("/entities")).text()

    assert deleted.status == 409
    assert "set pv.feed" in reason
    assert "solar_inverter" in listed, "the circuit is still there"


@pytest.mark.asyncio
async def test_a_save_the_loader_would_refuse_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """A panel needs at least one circuit, so deleting the last one cannot be saved."""
    config = default_config()
    config["circuits"] = [c for c in config["circuits"] if c["id"] == "solar_inverter"]
    app = _app(tmp_path, config)
    before = (tmp_path / _FILE).read_text(encoding="utf-8")

    async with TestClient(TestServer(app)) as client:
        await client.delete("/entities/solar_inverter")
        saved = await client.post("/save-reload")
        reason = await saved.text()

    assert saved.status == 422
    assert "At least one circuit must be defined" in reason
    assert (tmp_path / _FILE).read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_the_dashboard_starts_on_an_unloadable_active_config(tmp_path: Path) -> None:
    config = default_config()
    pv = config.get("pv")
    assert pv is not None
    pv["feed"] = "no_such_circuit"
    app = _app(tmp_path, config)

    async with TestClient(TestServer(app)) as client:
        page = await client.get("/")
        html = await page.text()

    assert page.status == 200
    assert "pv.feed &#39;no_such_circuit&#39; names no circuit" in html
    assert app[APP_KEY_DASHBOARD_CONTEXT].config_filter is None, (
        "nothing is open, so nothing can be saved over the file"
    )
