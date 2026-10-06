"""The battery link: a runtime control, carried on every tick, like the grid toggle."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, get_args

import pytest
from aiohttp.test_utils import TestClient, TestServer
from ebus_panel_sim import BESSCommunication

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.engine import DynamicSimulationEngine
from panelbench.panel import PanelInstance
from tests._helpers import DEFAULT_CONFIG, default_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from aiohttp import web

_DASHBOARD_CONFIG = """\
panel_config:
  serial_number: sim-link-0001
  total_tabs: 8
  main_size: 200
circuit_templates:
  plain:
    relay_behavior: controllable
    priority: OFF_GRID
    breaker_rating: 20
    energy_profile:
      mode: consumer
      power_range:
        min: 50
        max: 150
circuits:
- id: plain
  name: Plain
  template: plain
  tabs: [1]
"""


@pytest.mark.asyncio
async def test_the_tick_carries_the_requested_link() -> None:
    engine = DynamicSimulationEngine(config_path=DEFAULT_CONFIG)
    await engine.initialize_async()

    assert (await engine.get_tick_inputs()).bess_link == "OK"
    engine.set_bess_link("LOST")
    assert (await engine.get_tick_inputs()).bess_link == "LOST"


def test_an_unknown_link_health_is_refused() -> None:
    engine = DynamicSimulationEngine(config_path=DEFAULT_CONFIG)
    with pytest.raises(ValueError, match="battery link must be one of"):
        engine.set_bess_link("FLAKY")


@pytest.mark.asyncio
async def test_the_summary_reports_the_published_link(
    tmp_path: Path, amqtt_broker: tuple[str, int]
) -> None:
    """The dashboard shows what the wire carries, not what was asked for: a battery
    disabled mid-run is still published, out of the tick, as ``OK``."""
    host, port = amqtt_broker
    config = default_config()
    config["broker"] = {"host": host, "port": port}
    panel = PanelInstance(write_config(tmp_path / "panel.yaml", config), tick_interval=3600)
    await panel.start()
    try:
        assert panel.engine is not None
        assert panel.runtime is not None
        panel.engine.set_bess_link("LOST")
        await emitter_runtime.publish_tick(panel.runtime)
        summary = panel.get_power_summary()
        assert summary is not None
        assert summary["bess_link"] == "LOST"

        panel.update_bess_config({"enabled": False})
        await emitter_runtime.publish_tick(panel.runtime)
        summary = panel.get_power_summary()
        assert summary is not None
        assert panel.engine.bess_link == "LOST"
        assert summary["bess_link"] == "OK"
    finally:
        await panel.stop()


@pytest.fixture
def client_and_calls(tmp_path: Path) -> tuple[web.Application, list[str]]:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "panel.yaml").write_text(_DASHBOARD_CONFIG, encoding="utf-8")
    calls: list[str] = []
    ctx = DashboardContext(
        config_dir=cfg_dir,
        config_filter="panel.yaml",
        get_panel_configs=lambda: {},
        get_panel_ports=lambda: {},
        request_reload=lambda: None,
        set_bess_link=calls.append,
    )
    return create_dashboard_app(ctx), calls


@pytest.mark.asyncio
@pytest.mark.parametrize(("sent", "stored"), [("LOST", "LOST"), ("degraded", "DEGRADED")])
async def test_the_endpoint_sets_the_link(
    client_and_calls: tuple[web.Application, list[str]], sent: str, stored: str
) -> None:
    app, calls = client_and_calls
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/set-bess-link", json={"link": sent})
        assert resp.status == 200
        assert await resp.json() == {"ok": True, "link": stored}
    assert calls == [stored]


@pytest.mark.asyncio
async def test_an_unknown_link_never_reaches_the_engine(
    client_and_calls: tuple[web.Application, list[str]],
) -> None:
    app, calls = client_and_calls
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/set-bess-link", json={"link": "FLAKY"})
        assert resp.status == 400
        assert "FLAKY" in await resp.text()
    assert calls == []


@pytest.mark.asyncio
async def test_the_dashboard_offers_every_link_health(
    client_and_calls: tuple[web.Application, list[str]],
) -> None:
    """The control is rendered, hidden until a summary reports a battery, and its
    options are exactly the emitter's link healths."""
    app, _calls = client_and_calls
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        page = await resp.text()

    assert 'id="bess-link-control" style="display:none"' in page
    select = re.search(r'<select id="bess-link-select"[^>]*>(.*?)</select>', page, re.DOTALL)
    assert select is not None, "the control renders its select"
    assert set(re.findall(r'<option value="([A-Z]+)">', select.group(1))) == set(
        get_args(BESSCommunication)
    )
    assert "'set-bess-link'" in page
