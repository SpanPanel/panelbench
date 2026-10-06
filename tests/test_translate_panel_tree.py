"""A published tree, read back by the translator that clones a live panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.clone import TYPE_CIRCUIT, translate_panel_tree
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from panelbench.validation import validate_yaml_config
from tests._helpers import DEFAULT_CONFIG, default_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_SERIAL = "sim-40t-001"
# Not the shipped config's "Span Panel", so a translator that wrote that default
# instead of reading the published name would fail here.
_PANEL_NAME = "Garage Panel"


async def test_every_described_device_is_discovered() -> None:
    retained = await capture_retained(DEFAULT_CONFIG)
    devices = discovered_devices(retained)

    described = {t.split("/")[2] for t in retained if t.endswith("/$description")}
    assert set(devices) == described
    assert devices[_SERIAL].root_id == _SERIAL


async def test_panelbench_reads_its_own_wire_back_into_a_valid_config() -> None:
    devices = discovered_devices(await capture_retained(DEFAULT_CONFIG))

    config = translate_panel_tree(_SERIAL, devices)

    validate_yaml_config(config)
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    published = [d for d in devices.values() if (d.description or {}).get("type") == TYPE_CIRCUIT]
    assert len(circuits) == len(published)


async def test_a_translated_panel_keeps_its_published_name(tmp_path: Path) -> None:
    config = default_config()
    config["panel_config"]["display_name"] = _PANEL_NAME
    source = write_config(tmp_path / "panel.yaml", config)
    devices = discovered_devices(await capture_retained(source))
    assert (devices[_SERIAL].description or {})["name"] == _PANEL_NAME

    panel_config = translate_panel_tree(_SERIAL, devices)["panel_config"]

    assert isinstance(panel_config, dict)
    assert panel_config["display_name"] == _PANEL_NAME


async def test_the_grid_can_be_down_for_a_capture() -> None:
    retained = await capture_retained(DEFAULT_CONFIG, grid_online=False)
    mids = [t for t in retained if t.endswith("/grid/grid-state")]
    assert mids, "the default config publishes a MID"
    assert {retained[t].decode() for t in mids} == {"DOWN"}
