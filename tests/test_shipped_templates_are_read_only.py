"""The dashboard opens every template read-only: the tracked ones and the generated ones.

Each is rewritten at every start, the tracked ones by the add-on's ``run.sh`` and the
generated ones by ``write_reference_templates``, so an edit to one would be lost; a user
clones it to run a panel of their own. Each is opened from its file as a start lays it
down, so a template the dashboard could not load fails here too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.reference_templates import clone_name
from tests._helpers import EVERY_TEMPLATE

if TYPE_CHECKING:
    from pathlib import Path

    from aiohttp import web


def _app(tmp_path: Path, template_dir: Path, template: str) -> web.Application:
    """The dashboard viewing *template*, alone in its config directory."""
    (tmp_path / template).write_bytes((template_dir / template).read_bytes())
    return create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter=template,
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template", EVERY_TEMPLATE)
async def test_a_shipped_template_is_listed_as_one_and_not_saved_over(
    tmp_path: Path, template_dir: Path, template: str
) -> None:
    app = _app(tmp_path, template_dir, template)
    before = (tmp_path / template).read_bytes()

    async with TestClient(TestServer(app)) as client:
        listed = await (await client.get("/panels-list")).text()
        saved = await client.post("/save-reload")

    assert ">template</span>" in listed
    # The name the listing offers for a clone is the one the dashboard's rule gives;
    # what that rule gives is pinned by name in test_reference_templates.
    assert f"cloneConfig(this,'{template}','{clone_name(template)}')" in listed
    assert saved.status == 400
    assert (tmp_path / template).read_bytes() == before
