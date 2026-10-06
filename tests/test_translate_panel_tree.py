"""A published tree, read back by the translator that clones a live panel."""

from __future__ import annotations

from pathlib import Path

import pytest

from panelbench.clone import TYPE_CIRCUIT, translate_panel_tree
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from panelbench.validation import validate_yaml_config

pytestmark = pytest.mark.asyncio

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "default_MAIN_40.yaml"
_SERIAL = "sim-40t-001"


async def test_every_described_device_is_discovered() -> None:
    retained = await capture_retained(_CONFIG)
    devices = discovered_devices(retained)

    described = {t.split("/")[2] for t in retained if t.endswith("/$description")}
    assert set(devices) == described
    assert devices[_SERIAL].root_id == _SERIAL


async def test_panelbench_reads_its_own_wire_back_into_a_valid_config() -> None:
    devices = discovered_devices(await capture_retained(_CONFIG))

    config = translate_panel_tree(_SERIAL, devices)

    validate_yaml_config(config)
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    published = [d for d in devices.values() if (d.description or {}).get("type") == TYPE_CIRCUIT]
    assert len(circuits) == len(published)


async def test_a_translated_panel_keeps_its_published_name() -> None:
    devices = discovered_devices(await capture_retained(_CONFIG))

    panel_config = translate_panel_tree(_SERIAL, devices)["panel_config"]

    assert isinstance(panel_config, dict)
    assert panel_config["display_name"] == (devices[_SERIAL].description or {})["name"]


async def test_the_grid_can_be_down_for_a_capture() -> None:
    retained = await capture_retained(_CONFIG, grid_online=False)
    mids = [t for t in retained if t.endswith("/grid/grid-state")]
    assert mids, "the default config publishes a MID"
    assert {retained[t].decode() for t in mids} == {"DOWN"}
