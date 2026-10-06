"""One PV device per inverter, as SPAN firmware publishes from release 202639.

A panel with one PV circuit publishes one PV device under every firmware, with the
id it always had, as SPAN's CHANGELOG says a single-inverter panel keeps its id; the
flat-to-eBus upgrade rehearsal depends on it. A panel with two or more publishes one
device per circuit from 202639, or with no release named, and the single aggregate
device fed by the first PV circuit before 202639. A PV circuit's own identity wins
over the top-level `pv` section, which describes the first inverter; its firmware
version is not identity, so it is every inverter's default.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path

import pytest
from ebus_panel_sim import DeviceInstance
from ebus_sdk import DiscoveredDevice

from panelbench.clone import TYPE_PV
from panelbench.config_types import SimulationConfig
from panelbench.emitter_adapter.instance_ids import (
    pv_device_id,
    pv_inverter_device_id,
    stable_circuit_uuid,
)
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from tests._helpers import (
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
    default_config,
    source_and_clone,
    write_config,
)

_SERIAL = default_config()["panel_config"]["serial_number"]


def _panel(firmware: str, *, two_inverters: bool = False) -> SimulationConfig:
    """The default panel at *firmware*, plus a second inverter's circuit if asked."""
    config = default_config()
    config["firmware_version"] = firmware
    if two_inverters:
        used = {tab for circuit in config["circuits"] for tab in circuit["tabs"]}
        free = [tab for tab in config["unmapped_tabs"] if tab not in used][:2]
        assert len(free) == 2, "the default config needs two unmapped tabs for this test"
        config["unmapped_tabs"] = [t for t in config["unmapped_tabs"] if t not in free]
        templates = config["circuit_templates"]
        templates["solar_garage"] = copy.deepcopy(templates["solar"])
        config["circuits"].append(
            {
                "id": "solar_garage",
                "name": "Solar Garage",
                "template": "solar_garage",
                "tabs": free,
                "vendor": "SolarEdge",
                "model": "SE3800H-US",
            }
        )
    return config


def _pvs(config: SimulationConfig) -> list[DeviceInstance]:
    return [i for i in build_manifest(config).instances if i.entity_class == "pv"]


def _pv_info(devices: Mapping[str, DiscoveredDevice], property_id: str) -> list[str]:
    """Every published PV device's `info/<property_id>`, sorted."""
    return sorted(
        device.get_property("info", property_id) or ""
        for device in devices.values()
        if (device.description or {}).get("type") == TYPE_PV
    )


@pytest.mark.parametrize("firmware", [EARLIER_FIRMWARE, CURRENT_FIRMWARE, "sim/v0.1.0"])
def test_one_pv_circuit_keeps_its_id_under_every_firmware(firmware: str) -> None:
    config = _panel(firmware)

    [pv] = _pvs(config)

    assert pv.instance_id == pv_device_id(_SERIAL, config.get("pv"))


@pytest.mark.parametrize("firmware", [CURRENT_FIRMWARE, "sim/v0.1.0"])
def test_two_pv_circuits_are_two_devices_from_202639(firmware: str) -> None:
    [first, second] = _pvs(_panel(firmware, two_inverters=True))

    assert first.instance_id == pv_inverter_device_id(
        _SERIAL, "IQ8PLUS-72-2-US", stable_circuit_uuid(_SERIAL, "solar_inverter")
    )
    assert second.instance_id == pv_inverter_device_id(
        _SERIAL, "SE3800H-US", stable_circuit_uuid(_SERIAL, "solar_garage")
    )
    assert (first.display_name, second.display_name) == ("Solar", "Solar 2")
    assert second.metadata["vendor-name"] == "SolarEdge"
    assert second.metadata["model"] == "SE3800H-US"
    assert second.metadata["feed"] == stable_circuit_uuid(_SERIAL, "solar_garage")


def test_two_pv_circuits_are_one_device_before_202639() -> None:
    config = _panel(EARLIER_FIRMWARE, two_inverters=True)

    [pv] = _pvs(config)

    assert pv.instance_id == pv_device_id(_SERIAL, config.get("pv"))
    assert pv.metadata["feed"] == stable_circuit_uuid(_SERIAL, "solar_inverter")


@pytest.mark.asyncio
async def test_an_earlier_panel_with_two_pv_circuits_starts_with_one_pv_device(
    tmp_path: Path,
) -> None:
    """The config loads, the emitter accepts it, and one aggregate inverter is published."""
    path = write_config(tmp_path / "panel.yaml", _panel(EARLIER_FIRMWARE, two_inverters=True))

    devices = discovered_devices(await capture_retained(path))

    assert _pv_info(devices, "model") == ["IQ8PLUS-72-2-US"]


def test_a_pv_circuit_identity_wins_over_the_pv_section() -> None:
    config = _panel(CURRENT_FIRMWARE)
    for circuit in config["circuits"]:
        if circuit["id"] == "solar_inverter":
            circuit["model"] = "IQ8M-72-2-US"

    [pv] = _pvs(config)

    assert pv.metadata["model"] == "IQ8M-72-2-US"
    assert pv.instance_id == pv_device_id(_SERIAL, config.get("pv")), "the id does not move"


@pytest.mark.parametrize("two_inverters", [False, True])
def test_the_pv_section_feed_still_pins_the_first_inverter(two_inverters: bool) -> None:
    """`pv.feed` overrides the first inverter's feed on both paths, as it always has."""
    config = _panel(CURRENT_FIRMWARE, two_inverters=two_inverters)
    pv_section = config.get("pv")
    assert pv_section is not None
    pv_section["feed"] = "pinned-feed"

    first = _pvs(config)[0]

    assert first.metadata["feed"] == "pinned-feed"


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("IQ8PLUS-72-2-US", "sim-40t-001-iq8plus-72-2-us-c1"),
        ("SE 3800H/US", "sim-40t-001-se-3800h-us-c1"),
        (None, "sim-40t-001-pv-c1"),
    ],
)
def test_the_inverter_id_is_slugged(identifier: str | None, expected: str) -> None:
    assert pv_inverter_device_id("sim-40t-001", identifier, "c1") == expected


def test_only_the_identifier_is_slugged() -> None:
    """The panel and circuit ids are device ids already, and keep their case."""
    expected = "SIM-40T-001-se-3800h-cAfE01"
    assert pv_inverter_device_id("SIM-40T-001", "SE 3800H", "cAfE01") == expected


@pytest.mark.asyncio
async def test_a_clone_republishes_every_inverter(tmp_path: Path) -> None:
    source, clone = await source_and_clone(tmp_path, _panel(CURRENT_FIRMWARE, two_inverters=True))

    assert (
        _pv_info(clone, "model") == _pv_info(source, "model") == ["IQ8PLUS-72-2-US", "SE3800H-US"]
    )


@pytest.mark.asyncio
async def test_a_clone_keeps_a_single_inverter_identity(tmp_path: Path) -> None:
    source, clone = await source_and_clone(tmp_path, _panel(CURRENT_FIRMWARE))

    assert _pv_info(clone, "model") == _pv_info(source, "model") == ["IQ8PLUS-72-2-US"]


def _pv_firmware(config: SimulationConfig) -> str:
    pv_section = config.get("pv")
    assert pv_section is not None
    return pv_section["firmware_version"]


def test_every_inverter_publishes_the_pv_section_firmware() -> None:
    """A firmware version is not identity, so the `pv` section's is every inverter's."""
    config = _panel(CURRENT_FIRMWARE, two_inverters=True)

    firmware = [pv.metadata["firmware-version"] for pv in _pvs(config)]

    assert firmware == [_pv_firmware(config)] * 2


@pytest.mark.parametrize("two_inverters", [False, True])
def test_a_pv_circuit_firmware_wins_over_the_pv_section(two_inverters: bool) -> None:
    config = _panel(CURRENT_FIRMWARE, two_inverters=two_inverters)
    last_inverter = "solar_garage" if two_inverters else "solar_inverter"
    for circuit in config["circuits"]:
        if circuit["id"] == last_inverter:
            circuit["firmware_version"] = "inverter/v9.9.9"

    pvs = _pvs(config)

    assert pvs[-1].metadata["firmware-version"] == "inverter/v9.9.9"
    if two_inverters:
        assert pvs[0].metadata["firmware-version"] == _pv_firmware(config)


@pytest.mark.asyncio
async def test_a_clone_republishes_every_inverter_firmware(tmp_path: Path) -> None:
    config = _panel(CURRENT_FIRMWARE, two_inverters=True)
    garage = config["circuits"][-1]
    assert garage["id"] == "solar_garage"
    garage["firmware_version"] = "inverter/v9.9.9"

    source, clone = await source_and_clone(tmp_path, config)

    expected = sorted([_pv_firmware(config), "inverter/v9.9.9"])
    assert _pv_info(clone, "firmware-version") == _pv_info(source, "firmware-version") == expected
