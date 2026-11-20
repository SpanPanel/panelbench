"""PanelBench publishes the device ids a real panel does, by hardware and release.

A hardware 3.0 panel names its circuits by SPAN's number for each, ``<n>``, which it
keeps across releases while the id around it changes at release 202645:
``circuit-<n>`` before it, ``{serial}-{hex16}-1-16-<n>`` from it, ``remote-ct-<n>``
and ``…-1-42-<n>`` for a meter outside the panel. A config records the numbers and
the panel's 16-hex segment, and the ids follow the release, as a real panel's do.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

import pytest
import yaml

from panelbench.config_defaults import normalize_config
from panelbench.device_numbers import hex_of, next_number, number_of
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import recorded_panel
from panelbench.reference_templates import reference_template
from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS, PANEL_CONFIG, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.config_types import SimulationConfig

_MLO_48: Final = CONFIGS / "ref_MLO_48_TANDEMS.yaml"


def _raw(path: Path) -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _circuits(config: dict[str, object]) -> list[dict[str, object]]:
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    return circuits


@pytest.mark.parametrize("number", [0, -1, 1.5, "3", True])
def test_a_device_number_must_be_a_whole_number_above_0(number: object) -> None:
    config = _raw(_MLO_48)
    _circuits(config)[0]["circuit_number"] = number

    with pytest.raises(
        ValueError,
        match=r"circuit_number must be SPAN's number for the circuit, a whole number above 0",
    ):
        validate_yaml_config(config)


@pytest.mark.parametrize("on", ["circuit", "outside meter"])
def test_device_numbers_are_unique_on_a_panel(on: str) -> None:
    config = _raw(_MLO_48)
    first, second = _circuits(config)[:2]
    first["circuit_number"] = 12
    if on == "circuit":
        second["circuit_number"] = 12
        other = str(second["id"])
    else:
        config["outside_meters"] = [{"id": "service_ct", "circuit_number": 12}]
        other = "service_ct"

    with pytest.raises(ValueError, match=r"circuit_number 12 is taken by") as refused:
        validate_yaml_config(config)

    assert str(first["id"]) in str(refused.value)
    assert other in str(refused.value)


@pytest.mark.parametrize("segment", ["ABCDEF0123456789", "abcdef012345678"])
def test_the_id_segment_is_16_lowercase_hex_digits(segment: str) -> None:
    config = _raw(_MLO_48)
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    panel["device_id_hex"] = segment

    with pytest.raises(ValueError, match=r"device_id_hex must be 16 lowercase hex digits"):
        validate_yaml_config(config)


def test_numbers_and_a_segment_that_are_well_formed_load() -> None:
    config = _raw(_MLO_48)
    for number, circuit in enumerate(_circuits(config), start=1):
        circuit["circuit_number"] = number * 2
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    panel["device_id_hex"] = "d3c25547dc2d6690"

    validate_yaml_config(config)


@pytest.mark.parametrize(
    ("device_id", "number"),
    [
        ("circuit-12", 12),
        ("remote-ct-3", 3),
        ("xg-6159-74dlx-d3c25547dc2d6690-1-16-1", 1),
        ("xg-6159-74dlx-d3c25547dc2d6690-1-42-3", 3),
        ("31f1d56b0926276e04c84360b1a31a6c", None),
        ("xg-6159-74dlx-d3c25547dc2d6690-1-21-2", None),
        ("xg-6159-74dlx-lugs-up", None),
    ],
)
def test_a_device_s_number_is_read_from_its_id(device_id: str, number: int | None) -> None:
    assert number_of(device_id) == number


def test_a_panel_s_segment_is_read_from_its_devices_ids() -> None:
    ids = ["xg-6159-74dlx", "circuit-1", "xg-6159-74dlx-d3c25547dc2d6690-1-16-1"]

    assert hex_of("xg-6159-74dlx", ids) == "d3c25547dc2d6690"
    assert hex_of("xg-6159-74dlx", ["circuit-1", "xg-6159-74dlx-lugs-up"]) is None
    assert hex_of("another-panel", ids) is None


def test_the_next_number_is_the_lowest_unused() -> None:
    config = _raw(_MLO_48)
    circuits = _circuits(config)
    for circuit, number in zip(circuits[:3], (1, 2, 4), strict=True):
        circuit["circuit_number"] = number

    assert next_number(config) == 3
    assert next_number(_raw(_MLO_48)) is None


_HEX: Final = "d3c25547dc2d6690"
_R202639: Final = "spanos3/r202639/03"
_R202645: Final = "spanos3/r202645/02"


def _numbered(path: Path, *, firmware: str | None, hex16: str | None = _HEX) -> dict[str, object]:
    """The config at *path* on *firmware* (none where None), its circuits numbered
    5, 7, 9 and on in list order and an outside meter numbered 3 where the variant
    publishes one."""
    config = _raw(path)
    if firmware is None:
        config.pop("firmware_version", None)
    else:
        config["firmware_version"] = firmware
    for index, circuit in enumerate(_circuits(config)):
        circuit["circuit_number"] = 5 + 2 * index
    if config.get("hardware_version") == "3.0":
        config["outside_meters"] = [{"id": "service_ct", "circuit_number": 3}]
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    if hex16 is not None:
        panel["device_id_hex"] = hex16
    return config


def _manifest_ids(config: dict[str, object]) -> dict[str, str]:
    """Each device the definition holds, by its instance id, to its class."""
    typed = cast("SimulationConfig", config)
    normalize_config(typed, source="test")
    return {d.instance_id: d.entity_class for d in build_manifest(typed).instances}


def _serial(config: dict[str, object]) -> str:
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    return str(panel["serial_number"])


@pytest.mark.parametrize(
    "firmware", [_R202639, _R202645, None], ids=["r202639", "r202645", "naming no release"]
)
def test_a_hardware_3_panel_names_its_devices_as_its_release_does(firmware: str | None) -> None:
    config = _numbered(_MLO_48, firmware=firmware)
    serial = _serial(config)
    first = str(_circuits(config)[0]["id"])

    ids = _manifest_ids(config)

    lugs = {i for i, kind in ids.items() if kind == "lugs"}
    # A meter outside the panel is a remote-ct instance, published as a circuit device.
    circuits = {i for i, kind in ids.items() if kind in ("circuit", "remote-ct")}
    if firmware == _R202639:
        assert "circuit-5" in circuits
        assert "remote-ct-3" in circuits
        assert lugs == {f"{serial}-lugs-up", f"{serial}-lugs-dn"}
        assert all(re.fullmatch(r"(circuit|remote-ct)-\d+", i) for i in circuits)
    else:
        assert f"{serial}-{_HEX}-1-16-5" in circuits
        assert f"{serial}-{_HEX}-1-42-3" in circuits
        assert lugs == {f"{serial}-{_HEX}-1-21-2", f"{serial}-{_HEX}-1-21-1"}
        assert all(re.fullmatch(rf"{re.escape(serial)}-{_HEX}-1-(16|42)-\d+", i) for i in circuits)
    assert stable_circuit_uuid(serial, first) not in circuits


def _segments(serial: str) -> set[str]:
    """The 16-hex segments a numbered panel with no recorded one publishes under."""
    config = _numbered(_MLO_48, firmware=_R202645, hex16=None)
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    panel["serial_number"] = serial
    found: set[str] = set()
    for device_id, kind in _manifest_ids(config).items():
        match = re.fullmatch(rf"{re.escape(serial)}-([0-9a-f]{{16}})-1-\d+-\d+", device_id)
        if kind == "circuit":
            assert match is not None, device_id
            found.add(match[1])
    return found


def test_a_panel_with_no_recorded_segment_takes_one_of_its_own() -> None:
    """One segment for all its devices, the same on every start, and another panel's
    is its own."""
    (segment,) = _segments("sim-one")

    assert _segments("sim-one") == {segment}
    assert _segments("sim-two") != {segment}


@pytest.mark.parametrize("firmware", [_R202639, _R202645])
def test_a_main_32_s_ids_never_change(firmware: str) -> None:
    """A MAIN 32 publishes the same circuit ids on every release, numbered or not."""
    numbered = _numbered(PANEL_CONFIG, firmware=firmware)
    plain = _raw(PANEL_CONFIG)
    plain["firmware_version"] = firmware

    assert _manifest_ids(numbered) == _manifest_ids(plain)


def test_a_partly_numbered_panel_names_each_circuit_its_own_way() -> None:
    """A circuit with no number keeps its hashed id, and a tandem's lists name each
    mate by the id that mate publishes."""
    config = _raw(_MLO_48)
    config["firmware_version"] = _R202645
    (mate,) = [c for c in _circuits(config) if c["id"] == "circuit_37"]
    mate["circuit_number"] = 37
    serial = _serial(config)
    hashed = stable_circuit_uuid(serial, "circuit_37_2")
    typed = cast("SimulationConfig", config)
    normalize_config(typed, source="test")

    devices = {d.instance_id: d for d in build_manifest(typed).instances}

    numbered = next(i for i in devices if i.endswith("-1-16-37"))
    assert hashed in devices
    assert devices[numbered].metadata["shared-with-device-ids"] == hashed
    assert devices[hashed].metadata["shared-with-device-ids"] == numbered


@pytest.mark.asyncio
async def test_feeds_tandem_lists_and_ticks_name_the_published_ids(tmp_path: Path) -> None:
    """Every id a device's metadata or a tick names is one the panel publishes."""
    config = reference_template("r202645-g")
    for index, circuit in enumerate(_circuits(config)):
        circuit["circuit_number"] = 11 + index
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    await emitter_runtime.publish_tick(runtime)
    serial = runtime.engine.config["panel_config"]["serial_number"]
    children = set(json.loads(recorder.retained[f"ebus/5/{serial}/$description"])["children"])

    named = {
        value
        for device in runtime.manifest.instances
        for key in ("feed", "shared-with-device-ids")
        for value in str(device.metadata.get(key, "")).split(",")
        if value
    }
    tick = await runtime.engine.get_tick_inputs()

    assert named, "the template names a feed"
    assert named <= children
    assert set(tick.circuits) <= children
    assert all(re.fullmatch(r".+-1-(16|42)-\d+", i) for i in tick.circuits)
    # Each published circuit maps back to its config id, as relay commands are read.
    config_ids = {str(c["id"]) for c in runtime.engine.config["circuits"]}
    assert set(runtime.uuid_to_circuit_id) == set(tick.circuits)
    assert set(runtime.uuid_to_circuit_id.values()) == config_ids
    # A SPAN Drive reads the circuit feeding it, found by that circuit's published id.
    drives = runtime.manifest.of_class("evse")
    assert drives
    # Each circuit a power of its own, so a drive reading another's, or none, shows.
    powers = {device_id: 100.0 + n for n, device_id in enumerate(sorted(tick.circuits))}
    readings = emitter_runtime._evse_tick_inputs(runtime.engine.config, powers)
    for drive in drives:
        assert readings[drive.instance_id] == powers[drive.metadata["feed"]]


def test_the_emitter_s_reasons_name_a_numbered_circuit_by_its_config_id() -> None:
    config = _numbered(_MLO_48, firmware=_R202645)
    first = _circuits(config)[0]
    first["unvalued"] = ["foo/bar"]

    with pytest.raises(ValueError, match=rf"refuses this panel: {first['id']} key 'unvalued'"):
        validate_yaml_config(config)


@pytest.mark.asyncio
async def test_ids_embed_the_serial_the_panel_publishes(tmp_path: Path) -> None:
    """The engine names a panel ``sim-``, and the root the ids carry is that serial."""
    config = _numbered(_MLO_48, firmware=_R202645)
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    panel["serial_number"] = "abc-123"
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    await emitter_runtime.publish_tick(runtime)

    children = json.loads(recorder.retained["ebus/5/sim-abc-123/$description"])["children"]

    numbered = [c for c in children if re.fullmatch(r".+-1-(16|42|21)-\d+", c)]
    assert numbered
    assert all(c.startswith(f"sim-abc-123-{_HEX}-1-") for c in numbered)
