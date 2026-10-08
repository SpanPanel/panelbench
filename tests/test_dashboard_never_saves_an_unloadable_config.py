"""The dashboard never saves a config nothing will load, and starts when one is active.

A dashboard action used to be able to persist a config the loader refuses: deleting
the circuit `pv.feed` names left the feed dangling, the save went through, and the
panel restart then refused the file. With that config active, an add-on restart
crashed while creating the dashboard, so the user had no screen to fix it from.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard import config_store as config_store_module
from panelbench.dashboard.keys import APP_KEY_DASHBOARD_CONTEXT
from panelbench.validation import validate_yaml_config
from tests._helpers import (
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
    default_config,
    name_firmware,
    write_config,
)

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig

_FILE = "panel.yaml"
_SHIPPED = Path(__file__).resolve().parents[1] / "configs"


def _with_inverters(firmware: str, *extra: str, feed: str | None) -> SimulationConfig:
    """The default panel at *firmware*, with a PV circuit for each of *extra* beside
    its own, and `pv.feed` set to *feed*."""
    config = default_config()
    name_firmware(config, firmware)
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
    """The dashboard, with *config* the active file beside the shipped templates."""
    for shipped in sorted(_SHIPPED.glob("default_*.yaml")):
        (tmp_path / shipped.name).write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
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

    assert deleted.status == 422
    assert "set pv.feed" in reason
    assert "solar_inverter" in listed, "the circuit is still there"


@pytest.mark.asyncio
async def test_deleting_the_last_circuit_is_refused(tmp_path: Path) -> None:
    """A panel needs at least one circuit, so the delete is refused, not a later save."""
    config = default_config()
    config["circuits"] = [c for c in config["circuits"] if c["id"] == "solar_inverter"]
    app = _app(tmp_path, config)

    async with TestClient(TestServer(app)) as client:
        deleted = await client.delete("/entities/solar_inverter")
        reason = await deleted.text()
        listed = await (await client.get("/entities")).text()

    assert deleted.status == 422
    assert "At least one circuit must be defined" in reason
    assert "solar_inverter" in listed


@pytest.mark.asyncio
async def test_a_save_still_refuses_a_config_the_panel_would_not_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every edit is validated as it is made, so this is a guard for a store changed
    some other way: the save answers 422 and writes nothing."""
    app = _app(tmp_path, default_config())
    before = (tmp_path / _FILE).read_text(encoding="utf-8")

    def refuse(_config: object) -> None:
        raise ValueError("refused for the test")

    monkeypatch.setattr(config_store_module, "validate_yaml_config", refuse)
    async with TestClient(TestServer(app)) as client:
        saved = await client.post("/save-reload")
        reason = await saved.text()

    assert saved.status == 422
    assert "refused for the test" in reason
    assert (tmp_path / _FILE).read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_the_dashboard_starts_on_an_unloadable_active_config(tmp_path: Path) -> None:
    app = _app(tmp_path, _unloadable())

    async with TestClient(TestServer(app)) as client:
        page = await client.get("/")
        html = await page.text()

    assert page.status == 200
    assert "pv.feed &#39;no_such_circuit&#39; names no circuit" in html
    # The first shipped template, read-only, rather than an empty panel, and never the
    # refused file, so nothing can be saved over it.
    assert app[APP_KEY_DASHBOARD_CONTEXT].config_filter == "default_MAIN_16.yaml"
    assert "sim-16t-001" in html


@pytest.mark.parametrize(
    ("path", "form"),
    [
        ("/load-config", {"config_file": "default_MAIN_16.yaml"}),
        ("/clone", {"filename": "copy.yaml", "source_file": "default_MAIN_16.yaml"}),
        ("/restart-panel", {"filename": "default_MAIN_16.yaml"}),
    ],
    ids=["load", "clone", "restart"],
)
@pytest.mark.asyncio
async def test_moving_the_editor_to_another_config_clears_the_load_error(
    tmp_path: Path, path: str, form: dict[str, str]
) -> None:
    app = _app(tmp_path, _unloadable())

    async with TestClient(TestServer(app)) as client:
        moved = await client.post(path, data=form)
        html = await (await client.get("/")).text()

    assert moved.status == 200
    assert app[APP_KEY_DASHBOARD_CONTEXT].load_error is None
    assert "was not opened" not in html


@pytest.mark.asyncio
async def test_cloning_an_unloadable_config_is_refused_with_the_reason(tmp_path: Path) -> None:
    write_config(tmp_path / "broken.yaml", _unloadable())
    app = _app(tmp_path, default_config())

    async with TestClient(TestServer(app)) as client:
        cloned = await client.post(
            "/clone", data={"filename": "copy.yaml", "source_file": "broken.yaml"}
        )
        reason = await cloned.text()

    assert cloned.status == 400
    assert "names no circuit" in reason
    assert not (tmp_path / "copy.yaml").exists()


def _unloadable() -> SimulationConfig:
    config = default_config()
    pv = config.get("pv")
    assert pv is not None
    pv["feed"] = "no_such_circuit"
    return config


def _lifecycle_app(tmp_path: Path, calls: list[tuple[str, str]]) -> web.Application:
    """The dashboard editing a good config, beside an unloadable `broken.yaml`, with
    each panel action recorded in *calls*."""
    write_config(tmp_path / "broken.yaml", _unloadable())
    write_config(tmp_path / _FILE, default_config())
    return create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter=_FILE,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
            start_panel=lambda name: calls.append(("start", name)),
            stop_panel=lambda name: calls.append(("stop", name)),
            restart_panel=lambda name: calls.append(("restart", name)),
        )
    )


@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.asyncio
async def test_an_unloadable_config_is_not_started_or_restarted(tmp_path: Path, verb: str) -> None:
    """Starting it would make it the config the next add-on boot tries; restarting it
    would take a running panel down for a start the engine refuses."""
    calls: list[tuple[str, str]] = []
    app = _lifecycle_app(tmp_path, calls)

    async with TestClient(TestServer(app)) as client:
        answered = await client.post(f"/{verb}-panel", data={"filename": "broken.yaml"})
        reason = await answered.text()

    assert answered.status == 400
    assert "names no circuit" in reason
    assert calls == []
    assert app[APP_KEY_DASHBOARD_CONTEXT].config_filter == _FILE


@pytest.mark.asyncio
async def test_an_unloadable_config_is_still_stopped(tmp_path: Path) -> None:
    """Stopping does not need the file, so it happens, and the page says the file was
    not opened."""
    calls: list[tuple[str, str]] = []
    app = _lifecycle_app(tmp_path, calls)

    async with TestClient(TestServer(app)) as client:
        answered = await client.post("/stop-panel", data={"filename": "broken.yaml"})
        page = await (await client.get("/")).text()

    context = app[APP_KEY_DASHBOARD_CONTEXT]
    assert answered.status == 200
    assert calls == [("stop", "broken.yaml")]
    assert context.config_filter == _FILE, "the editor stays where it was"
    assert context.load_error is not None
    assert context.load_error.startswith("Stopped broken.yaml; it was not opened in the editor:")
    assert "Stopped broken.yaml" in page


@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.asyncio
async def test_a_loadable_config_is_started_or_restarted_and_opened(
    tmp_path: Path, verb: str
) -> None:
    calls: list[tuple[str, str]] = []
    app = _lifecycle_app(tmp_path, calls)
    write_config(tmp_path / "good.yaml", default_config())

    async with TestClient(TestServer(app)) as client:
        answered = await client.post(f"/{verb}-panel", data={"filename": "good.yaml"})

    assert answered.status == 200
    assert calls == [(verb, "good.yaml")]
    assert app[APP_KEY_DASHBOARD_CONTEXT].config_filter == "good.yaml", "the editor moves"
