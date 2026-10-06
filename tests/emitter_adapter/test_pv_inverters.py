"""One PV device per inverter, as SPAN firmware publishes from release 202639.

A panel with one PV circuit publishes one PV device under every firmware, with the
id it always had, as SPAN's CHANGELOG says a single-inverter panel keeps its id; the
flat-to-eBus upgrade rehearsal depends on it. A panel with two or more publishes one
device per circuit from 202639, or with no release named, and before 202639 the
single aggregate device fed by the circuit `pv.feed` names.

The top-level `pv` section describes one inverter: the one whose circuit `pv.feed`
names, else the panel's only PV circuit's. Never the first in list order, because a
real panel's inverter is the one its feeding circuit names, wherever that circuit
sits. With several PV circuits and no `pv.feed` it describes none of them, and a
config naming a release before 202639 is refused, since that release's one device
needs a feed. A PV circuit's own identity wins over the section; its firmware version
is not identity, so `pv.firmware_version` is every inverter's default.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path

import pytest
from ebus_panel_sim import DeviceInstance
from ebus_sdk import DiscoveredDevice

from panelbench.clone import TYPE_PV, translate_panel_tree
from panelbench.config_types import PVConfigYAML, SimulationConfig
from panelbench.emitter_adapter.instance_ids import (
    pv_device_id,
    pv_inverter_device_id,
    stable_circuit_uuid,
)
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from panelbench.validation import validate_yaml_config
from tests._helpers import (
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
    default_config,
    source_and_clone,
    write_config,
)

_SERIAL = default_config()["panel_config"]["serial_number"]


def _panel(
    firmware: str, *, two_inverters: bool = False, pv_feed: str | None = "solar_inverter"
) -> SimulationConfig:
    """The default panel at *firmware*, plus a second inverter's circuit if asked.

    A second inverter's config sets `pv.feed` to *pv_feed*, the original inverter's
    circuit, as the README's rehearsal tells a user adding one to; `None` leaves the
    section bound to no circuit.
    """
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
        if pv_feed is not None:
            _pv_section(config)["feed"] = pv_feed
    return config


def _pv_section(config: SimulationConfig) -> PVConfigYAML:
    pv_section = config.get("pv")
    assert pv_section is not None
    return pv_section


def _reversed_circuits(config: SimulationConfig) -> SimulationConfig:
    """*config* with its circuits listed in reverse, which changes nothing about the panel."""
    config["circuits"] = list(reversed(config["circuits"]))
    return config


def _by_feed(config: SimulationConfig) -> dict[str, DeviceInstance]:
    """Every PV device *config* publishes, keyed by the circuit id that feeds it."""
    feeds = {stable_circuit_uuid(_SERIAL, c["id"]): c["id"] for c in config["circuits"]}
    return {feeds[pv.metadata["feed"]]: pv for pv in _pvs(config)}


def _identities(config: SimulationConfig) -> dict[str, tuple[str, Mapping[str, str]]]:
    """Each PV device's id and `info` metadata, keyed by the circuit id that feeds it."""
    return {feed: (pv.instance_id, pv.metadata) for feed, pv in _by_feed(config).items()}


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


@pytest.mark.parametrize("firmware", [CURRENT_FIRMWARE, "sim/v0.1.0"])
def test_reordering_circuits_does_not_move_the_pv_section_identity(firmware: str) -> None:
    """The section's inverter is the one `pv.feed` names, wherever its circuit is listed."""
    listed = _identities(_panel(firmware, two_inverters=True))
    reordered = _identities(_reversed_circuits(_panel(firmware, two_inverters=True)))

    assert reordered == listed
    assert reordered["solar_inverter"][1]["model"] == "IQ8PLUS-72-2-US"
    assert reordered["solar_garage"][1]["model"] == "SE3800H-US"


def test_reordering_circuits_does_not_move_the_earlier_firmware_feed() -> None:
    """Before 202639 the one aggregate device is fed by the circuit `pv.feed` names."""
    config = _reversed_circuits(_panel(EARLIER_FIRMWARE, two_inverters=True))

    [pv] = _pvs(config)

    assert pv.metadata["feed"] == stable_circuit_uuid(_SERIAL, "solar_inverter")
    assert pv.metadata["model"] == "IQ8PLUS-72-2-US"


def test_several_pv_circuits_without_pv_feed_are_refused_before_202639() -> None:
    config = _panel(EARLIER_FIRMWARE, two_inverters=True, pv_feed=None)

    with pytest.raises(ValueError, match=r"set pv\.feed") as refused:
        validate_yaml_config(config)

    assert "'solar_inverter'" in str(refused.value)
    assert "'solar_garage'" in str(refused.value)
    with pytest.raises(ValueError, match=r"set pv\.feed"):
        build_manifest(config)


@pytest.mark.parametrize("firmware", [CURRENT_FIRMWARE, "sim/v0.1.0"])
def test_several_pv_circuits_without_pv_feed_bind_the_section_to_none(firmware: str) -> None:
    """Each inverter is named by its own circuit, and none takes the section's identity."""
    config = _panel(firmware, two_inverters=True, pv_feed=None)
    _pv_section(config)["serial_number"] = "sim-inv-0001"
    validate_yaml_config(config)

    pvs = _by_feed(config)

    original = pvs["solar_inverter"]
    assert "model" not in original.metadata
    assert "serial-number" not in original.metadata
    assert original.instance_id == pv_inverter_device_id(
        _SERIAL, None, stable_circuit_uuid(_SERIAL, "solar_inverter")
    )
    assert "serial-number" not in pvs["solar_garage"].metadata
    assert all(pv.metadata["firmware-version"] == _pv_firmware(config) for pv in pvs.values()), (
        "a firmware version is not identity, so it still reaches every inverter"
    )


@pytest.mark.parametrize("feed_by", ["circuit id", "circuit device id"])
def test_pv_feed_names_the_inverter_the_section_describes(feed_by: str) -> None:
    """`pv.feed` names a circuit by its config `id`, or by the device id it publishes."""
    config = _panel(CURRENT_FIRMWARE, two_inverters=True)
    _pv_section(config)["feed"] = (
        "solar_garage" if feed_by == "circuit id" else stable_circuit_uuid(_SERIAL, "solar_garage")
    )
    _pv_section(config)["serial_number"] = "sim-inv-0002"
    validate_yaml_config(config)

    pvs = _by_feed(config)

    assert pvs["solar_garage"].metadata["serial-number"] == "sim-inv-0002"
    assert pvs["solar_garage"].metadata["model"] == "SE3800H-US", "the circuit's own wins"
    assert "serial-number" not in pvs["solar_inverter"].metadata
    assert "model" not in pvs["solar_inverter"].metadata


def test_pv_feed_feeds_the_earlier_firmware_device() -> None:
    config = _panel(EARLIER_FIRMWARE, two_inverters=True, pv_feed="solar_garage")

    [pv] = _pvs(config)

    assert pv.metadata["feed"] == stable_circuit_uuid(_SERIAL, "solar_garage")
    assert pv.metadata["model"] == "SE3800H-US"


def test_pv_feed_on_a_single_inverter_publishes_what_it_did_without() -> None:
    """Naming the only PV circuit, by either spelling, changes nothing on the wire."""
    unnamed = _pvs(_panel(CURRENT_FIRMWARE))
    by_id = _panel(CURRENT_FIRMWARE)
    _pv_section(by_id)["feed"] = "solar_inverter"
    by_device_id = _panel(CURRENT_FIRMWARE)
    _pv_section(by_device_id)["feed"] = stable_circuit_uuid(_SERIAL, "solar_inverter")

    assert _pvs(by_id) == unnamed
    assert _pvs(by_device_id) == unnamed


@pytest.mark.parametrize(
    ("feed", "reason"),
    [("no_such_circuit", "names no circuit"), ("kitchen_outlets_1", "is not a PV circuit")],
)
def test_pv_feed_must_name_a_pv_circuit(feed: str, reason: str) -> None:
    config = _panel(CURRENT_FIRMWARE, pv_feed=None)
    _pv_section(config)["feed"] = feed

    with pytest.raises(ValueError, match=reason):
        validate_yaml_config(config)
    with pytest.raises(ValueError, match=reason):
        build_manifest(config)


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
async def test_a_clone_of_several_inverters_names_each_on_its_circuit(tmp_path: Path) -> None:
    """The clone writes no `pv` section, so it binds no identity to a circuit by position."""
    source = _panel(CURRENT_FIRMWARE, two_inverters=True)
    source_path = write_config(tmp_path / "panel.yaml", source)
    devices = discovered_devices(await capture_retained(source_path))

    cloned = translate_panel_tree(_SERIAL, devices)

    assert "pv" not in cloned
    validate_yaml_config(cloned)
    models = sorted(
        str(circuit["model"]) for circuit in _circuit_list(cloned) if "model" in circuit
    )
    assert models == ["IQ8PLUS-72-2-US", "SE3800H-US"]


def _circuit_list(config: Mapping[str, object]) -> list[Mapping[str, object]]:
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    return [c for c in circuits if isinstance(c, dict)]


@pytest.mark.asyncio
async def test_a_clone_keeps_a_single_inverter_identity(tmp_path: Path) -> None:
    source, clone = await source_and_clone(tmp_path, _panel(CURRENT_FIRMWARE))

    assert _pv_info(clone, "model") == _pv_info(source, "model") == ["IQ8PLUS-72-2-US"]


def _pv_firmware(config: SimulationConfig) -> str:
    return _pv_section(config)["firmware_version"]


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
