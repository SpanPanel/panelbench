"""Export: a saved config's makeup as a panel definition."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.definition_export import definition_for_config_file, definition_text
from panelbench.definition_import import config_from_definition_text
from tests._helpers import default_config, write_config

pytestmark = pytest.mark.asyncio


def _with_serial(tmp_path: Path, serial: str) -> Path:
    """The default panel saved under *serial*."""
    config = default_config()
    config["panel_config"]["serial_number"] = serial
    return write_config(tmp_path / "panel.yaml", config)


async def test_the_definition_names_the_serial_the_panel_publishes(tmp_path: Path) -> None:
    definition = await definition_for_config_file(_with_serial(tmp_path, "40t-777"))

    assert definition.manifest.of_class("panel")[0].instance_id == "sim-40t-777"


async def test_an_exported_definition_imports_with_the_same_makeup(tmp_path: Path) -> None:
    definition = await definition_for_config_file(_with_serial(tmp_path, "sim-40t-001"))

    imported = config_from_definition_text(definition_text(definition))

    circuits = imported["circuits"]
    assert isinstance(circuits, list)
    assert len(circuits) == len(definition.manifest.of_class("circuit"))


def _dashboard(cfg_dir: Path, config_filter: str | None) -> web.Application:
    return create_dashboard_app(
        DashboardContext(
            config_dir=cfg_dir,
            config_filter=config_filter,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )


@pytest.fixture
def client_app(tmp_path: Path) -> web.Application:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    write_config(cfg_dir / "panel.yaml", default_config())
    return _dashboard(cfg_dir, "panel.yaml")


async def test_the_endpoint_exports_the_saved_config(client_app: web.Application) -> None:
    async with TestClient(TestServer(client_app)) as client:
        resp = await client.get("/export-definition")
        assert resp.status == 200
        assert "schema: panel-sim-definition/1" in await resp.text()
        assert 'filename="panel.definition.yaml"' in resp.headers["Content-Disposition"]


async def test_unsaved_edits_are_a_conflict(client_app: web.Application) -> None:
    async with TestClient(TestServer(client_app)) as client:
        await client.put("/sim-params", data={"update_interval": "7", "noise_factor": "0.02"})
        resp = await client.get("/export-definition")
        assert resp.status == 409


async def test_without_a_loaded_file_there_is_nothing_to_export(tmp_path: Path) -> None:
    async with TestClient(TestServer(_dashboard(tmp_path, None))) as client:
        resp = await client.get("/export-definition")
        assert resp.status == 400
        assert await resp.text() == "No config file is loaded"


async def test_a_saved_file_the_engine_rejects_is_a_bad_request(
    client_app: web.Application, tmp_path: Path
) -> None:
    """The file is read again at export, so an edit made outside the dashboard is
    reported, not raised as a server error."""
    (tmp_path / "cfg" / "panel.yaml").write_text("- not a config\n", encoding="utf-8")
    async with TestClient(TestServer(client_app)) as client:
        resp = await client.get("/export-definition")
        assert resp.status == 400
        assert "YAML configuration must be a dictionary" in await resp.text()
