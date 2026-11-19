"""The dashboard opens every shipped ``default_*`` template read-only.

The add-on refreshes them at every start (``run.sh``), so an edit to one would be lost;
a user clones it to run a panel of their own. Each is opened from its file as shipped,
so a template the dashboard could not load fails here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from tests._helpers import CONFIGS, SHIPPED_TEMPLATES

_SHIPPED = sorted(p.name for p in CONFIGS.glob("default_*.yaml")) + list(SHIPPED_TEMPLATES)


def _app(tmp_path: Path, template: str) -> web.Application:
    """The dashboard viewing the shipped *template*."""
    (tmp_path / template).write_bytes((CONFIGS / template).read_bytes())
    return create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter=template,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )


def test_the_tandem_templates_are_shipped() -> None:
    assert set(SHIPPED_TEMPLATES) <= set(_SHIPPED)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", _SHIPPED)
async def test_a_shipped_template_is_listed_as_one_and_not_saved_over(
    tmp_path: Path, template: str
) -> None:
    app = _app(tmp_path, template)
    before = (tmp_path / template).read_bytes()

    async with TestClient(TestServer(app)) as client:
        listed = await (await client.get("/panels-list")).text()
        saved = await client.post("/save-reload")

    assert ">template</span>" in listed
    clone_name = template.removeprefix("default_").removeprefix("ref_")
    assert f"cloneConfig(this,'{template}','{clone_name}')" in listed
    assert saved.status == 400
    assert (tmp_path / template).read_bytes() == before
