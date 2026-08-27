"""A never-backup circuit's shed priority is not the dashboard's to change.

`never-backup` is an installer commissioning lock, and the eBus schema migration guide
maps it onto exactly one thing: `load-shed/priority` publishes with
`$settable = !never-backup`. So the panel offers no consumer that write, and the
dashboard must not offer it either — the same argument that gates the relay toggle in
`test_relay_endpoint_honours_the_lock.py`.

There is a second reason here that the relay case does not have. The lock *is*
permanently `OFF_GRID`: `manifest_physics._parse_circuit` rejects `never-backup: true`
beside any other `default-priority` rather than silently rewriting either half. A saved
edit would therefore produce a config that cannot start the panel at all, which turns a
UI control into a way to break the simulator from the browser.

`priority == "NEVER"` is deliberately not this. It is an ordinary settable value meaning
"never shed", which a consumer chose and may unchoose.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app

pytestmark = pytest.mark.asyncio

_CONFIG = """\
panel_config:
  serial_number: sim-priority-gate
  total_tabs: 8
  main_size: 200
circuit_templates:
  pool_pump:
    relay_behavior: controllable
    priority: OFF_GRID
    never_backup: true
    breaker_rating: 20
    energy_profile:
      mode: consumer
      power_range:
        min: 50
        max: 150
  dishwasher:
    relay_behavior: controllable
    priority: NEVER
    breaker_rating: 20
    energy_profile:
      mode: consumer
      power_range:
        min: 50
        max: 150
circuits:
- id: pool_pump
  name: Pool Pump
  template: pool_pump
  tabs: [1]
- id: dishwasher
  name: Dishwasher
  template: dishwasher
  tabs: [3]
"""


@pytest.fixture
def client_and_calls(tmp_path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "panel.yaml").write_text(_CONFIG, encoding="utf-8")

    calls: list[tuple[str, str]] = []
    ctx = DashboardContext(
        config_dir=cfg_dir,
        config_filter="panel.yaml",
        get_panel_configs=lambda: {},
        get_panel_ports=lambda: {},
        request_reload=lambda: None,
        set_circuit_priority=lambda cid, priority: calls.append((cid, priority)),
    )
    return create_dashboard_app(ctx), calls


async def test_a_never_backup_circuit_refuses_a_priority_change(client_and_calls) -> None:
    app, calls = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.put("/entities/pool_pump", data={"priority": "SOC_THRESHOLD"})

        assert resp.status == 409
        assert "never-backup" in await resp.text()
    assert calls == []


async def test_resubmitting_the_same_priority_is_not_a_change(client_and_calls) -> None:
    """The edit form posts every field, including ones the user did not touch, so a save
    that leaves the priority alone must not be refused for carrying it."""
    app, _ = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/entities/pool_pump", data={"priority": "OFF_GRID", "name": "Pool Pump 2"}
        )

        assert resp.status == 200


async def test_an_ordinary_circuit_at_never_is_still_editable(client_and_calls) -> None:
    """`NEVER` is a value, not a lock. A circuit that merely opts out of shedding may be
    re-prioritised, which is exactly the case a priority-derived flag got wrong."""
    app, calls = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.put("/entities/dishwasher", data={"priority": "OFF_GRID"})

        assert resp.status == 200
    assert calls == [("dishwasher", "OFF_GRID")]


async def test_the_edit_form_disables_the_locked_select(client_and_calls) -> None:
    """A control that posts a value the server will refuse is worse than no control."""
    app, _ = client_and_calls

    async with TestClient(TestServer(app)) as client:
        locked = await (await client.get("/entities/pool_pump/edit")).text()
        open_ = await (await client.get("/entities/dishwasher/edit")).text()

    assert '<select name="priority" disabled>' in locked
    assert '<select name="priority" disabled>' not in open_
