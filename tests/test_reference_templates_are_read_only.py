"""The dashboard keeps a reference capture's ``ref_*`` template read-only, as it keeps
the shipped ``default_*`` ones: it is rebuilt at every start, so an edit to it would
be lost. A user clones it to run a panel of their own."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from tests._helpers import default_config, write_config

_TEMPLATE = "ref_MAIN_32.yaml"


def _app(tmp_path: Path) -> web.Application:
    """The dashboard viewing a ``ref_*`` template."""
    write_config(tmp_path / _TEMPLATE, default_config())
    return create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter=_TEMPLATE,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )


@pytest.mark.asyncio
async def test_a_reference_template_is_not_saved_over(tmp_path: Path) -> None:
    app = _app(tmp_path)
    before = (tmp_path / _TEMPLATE).read_text(encoding="utf-8")

    async with TestClient(TestServer(app)) as client:
        saved = await client.post("/save-reload")

    assert saved.status == 400
    assert (tmp_path / _TEMPLATE).read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_a_reference_template_is_not_deleted(tmp_path: Path) -> None:
    async with TestClient(TestServer(_app(tmp_path))) as client:
        answer = await (await client.post("/delete-config", data={"filename": _TEMPLATE})).text()

    assert "cannot be deleted" in answer
    assert (tmp_path / _TEMPLATE).exists()


@pytest.mark.asyncio
async def test_a_reference_template_is_listed_as_a_template_and_cloned_without_its_marker(
    tmp_path: Path,
) -> None:
    async with TestClient(TestServer(_app(tmp_path))) as client:
        listed = await (await client.get("/panels-list")).text()

    assert ">template</span>" in listed
    assert f"cloneConfig(this,'{_TEMPLATE}','MAIN_32.yaml')" in listed
