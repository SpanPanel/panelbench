"""The dashboard shows the inverters no circuit feeds, each a device of its own.

They are no circuit, so the entity list, which lists circuits, cannot show them;
without a card of their own a panel publishing two inverters looks like a panel
with none.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard.config_store import ConfigStore, InverterView

_PANEL = """\
panel_config:
  serial_number: sim-unfed-inverters
  total_tabs: 8
  main_size: 200
  latitude: 37.7
  longitude: -122.4
circuit_templates:
  lighting:
    energy_profile:
      mode: consumer
      power_range: [0.0, 500.0]
      typical_power: 80.0
      power_variation: 0.1
    relay_behavior: controllable
    priority: NEVER
circuits:
- id: light_1
  name: Light 1
  template: lighting
  tabs: [1]
simulation_params:
  update_interval: 5
  time_acceleration: 1.0
  noise_factor: 0.02
  enable_realistic_behaviors: true
"""

_TWO_INVERTERS = """\
pv:
  enabled: true
  inverters:
  - vendor: SolarEdge
    product_name: SE7600H-US
    nameplate_capacity_w: 11680.0
  - vendor: Fronius
    nameplate_capacity_w: 7600.0
    relative_position: IN_PANEL
"""

_ONE_SECTION_INVERTER = """\
pv:
  enabled: true
  vendor: Enphase
  product_name: IQ7PLUS
"""


def _store(extra: str) -> ConfigStore:
    store = ConfigStore()
    store.load_from_yaml(_PANEL + extra)
    return store


def test_each_listed_inverter_is_shown_as_configured() -> None:
    assert _store(_TWO_INVERTERS).list_unfed_inverters() == [
        InverterView("SolarEdge", "SE7600H-US", 11680.0, "UPSTREAM"),
        InverterView("Fronius", "", 7600.0, "IN_PANEL"),
    ]


def test_the_section_own_inverter_is_shown_on_a_panel_with_no_pv_circuit() -> None:
    assert _store(_ONE_SECTION_INVERTER).list_unfed_inverters() == [
        InverterView("Enphase", "IQ7PLUS", None, "UPSTREAM")
    ]


def test_a_panel_with_no_pv_section_shows_none() -> None:
    assert _store("").list_unfed_inverters() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/entities"])
async def test_the_dashboard_shows_a_card_of_them(tmp_path: Path, path: str) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "panel.yaml").write_text(_PANEL + _TWO_INVERTERS, encoding="utf-8")
    context = DashboardContext(
        config_dir=config_dir,
        config_filter="panel.yaml",
        get_panel_configs=dict,
        get_panel_ports=dict,
        request_reload=lambda: None,
    )

    async with TestClient(TestServer(create_dashboard_app(context))) as client:
        response = await client.get(path)
        assert response.status == 200
        body = await response.text()

    assert "Inverters No Circuit Feeds (2)" in body
    assert "SolarEdge SE7600H-US" in body
    assert "11680W nameplate" in body
    assert "Fronius" in body
    assert "in panel" in body
