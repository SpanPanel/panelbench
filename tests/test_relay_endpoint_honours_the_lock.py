"""`POST /entities/{id}/relay` must not offer a command the published tree forbids.

The override this endpoint drives is producer-side. `engine.set_dynamic_overrides`
lands in `circuit._apply_state_overrides`, where an `OPEN` relay zeroes the circuit's
power; it never reaches the emitter's `RelayResolver`, which is what decides the
published `switch/relay`. So on a circuit whose relay is locked, toggling it here used
to zero the power while the emitter went on publishing `switch/relay: CLOSED` with
`relay-controllable: false` and `relay-requester: CONFIGURATION` — a tree saying the
relay is closed and not commandable, next to a circuit drawing nothing, with this
endpoint answering `{"ok": true}` for a command that same tree says cannot be issued.

`capabilities/switch.md:28` makes `relay` settable "when `relay-controllable`", and
`switch.md:29` defines the flag as the relay being openable "by command or automatic
shed". A locked circuit is openable by neither, so there is no reading under which the
dashboard gets to open one.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app

pytestmark = pytest.mark.asyncio

_CONFIG = """\
panel_config:
  serial_number: sim-relay-gate
  total_tabs: 8
  main_size: 200
circuit_templates:
  freezer:
    relay_behavior: non_controllable
    priority: NEVER
    breaker_rating: 20
    energy_profile:
      mode: consumer
      power_range:
        min: 50
        max: 150
  always:
    relay_behavior: always_on
    priority: NEVER
    breaker_rating: 20
    energy_profile:
      mode: consumer
      power_range:
        min: 50
        max: 150
  dishwasher:
    relay_behavior: controllable
    priority: OFF_GRID
    breaker_rating: 20
    energy_profile:
      mode: consumer
      power_range:
        min: 50
        max: 150
circuits:
- id: chest_freezer
  name: Chest Freezer
  template: freezer
  tabs: [1]
- id: fire_pump
  name: Fire Pump
  template: always
  tabs: [3]
- id: dishwasher
  name: Dishwasher
  template: dishwasher
  tabs: [5]
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
        set_circuit_relay=lambda cid, state: calls.append((cid, state)),
    )
    return create_dashboard_app(ctx), calls


async def test_a_controllable_circuit_is_still_commandable(client_and_calls) -> None:
    app, calls = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/entities/dishwasher/relay", json={"relay_state": "OPEN"})

        assert resp.status == 200
        assert await resp.json() == {"ok": True, "relay_state": "OPEN"}
    assert calls == [("dishwasher", "OPEN")]


@pytest.mark.parametrize(
    ("entity_id", "spelling"),
    [("chest_freezer", "non_controllable"), ("fire_pump", "always_on")],
)
async def test_a_locked_circuit_is_refused(client_and_calls, entity_id, spelling) -> None:
    """Both spellings lock. `relay-behavior: always-on` and `non-controllable` are one
    commissioning flag on the hardware this models — SPAN publishes
    `relay-controllable = !always-on` — and `manifest_physics.relay_locked` treats them
    alike, so a gate that caught only one would still publish an unissuable offer."""
    app, calls = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/entities/{entity_id}/relay", json={"relay_state": "OPEN"})

        assert resp.status == 409
        body = await resp.text()
        assert spelling in body
        assert "relay-controllable=false" in body
    assert calls == [], "a refused command must not reach the producer-side override"


async def test_an_unknown_entity_is_a_404_not_a_lock_error(client_and_calls) -> None:
    """The lookup the gate needs is also the one that says whether the circuit exists.
    Reporting a missing circuit as locked would be a worse answer than the endpoint gave
    before, which silently accepted the toggle."""
    app, calls = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/entities/nonesuch/relay", json={"relay_state": "OPEN"})

        assert resp.status == 404
    assert calls == []


async def test_a_malformed_state_is_still_rejected_before_the_lock_is_consulted(
    client_and_calls,
) -> None:
    """400 for a bad payload, 409 for a good payload the circuit's commissioning refuses.
    Collapsing the two would tell a caller to fix the wrong thing."""
    app, calls = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/entities/chest_freezer/relay", json={"relay_state": "AJAR"})

        assert resp.status == 400
    assert calls == []


async def test_the_row_offers_no_toggle_on_a_locked_circuit(client_and_calls) -> None:
    """The click handler in `runtime_controls.html` binds to `.circuit-status-toggle` and
    flips the dot optimistically, before the response arrives. Rendering that class on a
    locked circuit would show the relay opening while the server refuses it — trading one
    incoherence for another rather than removing it."""
    app, _ = client_and_calls

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/entities")

        assert resp.status == 200
        body = await resp.text()

    rows = {
        cid: body.split(f'id="entity-{cid}"', 1)[1].split("</span>", 1)[0]
        for cid in ("chest_freezer", "fire_pump", "dishwasher")
    }
    assert "circuit-status-toggle" not in rows["chest_freezer"]
    assert "circuit-status-toggle" not in rows["fire_pump"]
    assert "circuit-status-toggle" in rows["dishwasher"]
