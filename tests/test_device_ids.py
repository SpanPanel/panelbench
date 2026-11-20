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
from ebus_panel_sim import load_reference_capture

from panelbench.clone import translate_panel_tree
from panelbench.config_defaults import normalize_config
from panelbench.dashboard.config_store import ConfigStore
from panelbench.definition_import import config_from_definition
from panelbench.device_numbers import hex_of, next_number, number_of
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import recorded_panel
from panelbench.reference_templates import reference_template
from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS, PANEL_CONFIG, discovered_from_tree, write_config

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


def test_a_circuit_number_is_used_once_among_a_panel_s_circuits() -> None:
    config = _raw(_MLO_48)
    first, second = _circuits(config)[:2]
    first["circuit_number"] = 12
    second["circuit_number"] = 12

    with pytest.raises(ValueError, match=r"circuit_number 12 is taken by") as refused:
        validate_yaml_config(config)

    assert str(first["id"]) in str(refused.value)
    assert str(second["id"]) in str(refused.value)


def test_a_circuit_and_a_remote_ct_may_share_a_number() -> None:
    """SPAN numbers a panel's circuits and its remote CTs apart: a MAIN 16 has both
    ``circuit-3`` and ``remote-ct-3``."""
    config = _raw(_MLO_48)
    _circuits(config)[0]["circuit_number"] = 3
    config["outside_meters"] = [{"id": "service_ct", "circuit_number": 3}]

    validate_yaml_config(config)


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
    # A remote CT's number is no circuit's, so a circuit added may take it.
    config["outside_meters"] = [{"id": "service_ct", "circuit_number": 3}]
    assert next_number(config) == 3


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


@pytest.mark.parametrize("firmware", [_R202639, _R202645])
def test_a_reason_names_the_circuit_whose_id_another_s_begins(firmware: str) -> None:
    """``circuit-1`` begins ``circuit-12``, as ``…-1-16-1`` begins ``…-1-16-12``, so a
    reason naming the second must name its circuit, not the first's."""
    config = _numbered(_MLO_48, firmware=firmware)
    first, second = _circuits(config)[:2]
    first["circuit_number"] = 1
    second["circuit_number"] = 12
    second["unvalued"] = ["foo/bar"]

    with pytest.raises(
        ValueError, match=rf"refuses this panel: {re.escape(str(second['id']))} key 'unvalued'"
    ):
        validate_yaml_config(config)


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


def _cloned(capture: str) -> dict[str, object]:
    """A clone of the panel *capture* records, as cloning that panel writes it."""
    tree = load_reference_capture(capture).tree
    (panel,) = [i for i, d in tree.items() if d.type == "distribution-enclosure"]
    return translate_panel_tree(panel, discovered_from_tree(tree))


# A circuit by its spaces and name: a tandem's circuits share their spaces.
_Key = tuple[tuple[int, ...], str]


def _tree_numbers(capture: str) -> dict[_Key, int]:
    """Each circuit's number in the capture's tree, by its spaces and name."""
    tree = load_reference_capture(capture).tree
    numbers: dict[_Key, int] = {}
    for device_id, device in tree.items():
        number = number_of(device_id)
        spaces = device.value("info/spaces")
        if device.type == "circuit" and number is not None and spaces:
            key = (tuple(int(s) for s in str(spaces).split(",")), str(device.value("info/name")))
            numbers[key] = number
    return numbers


def _cloned_numbers(config: dict[str, object]) -> dict[_Key, int]:
    numbers: dict[_Key, int] = {}
    for circuit in _circuits(config):
        tabs = circuit.get("tabs")
        number = circuit.get("circuit_number")
        if isinstance(tabs, list) and isinstance(number, int):
            numbers[(tuple(tabs), str(circuit["name"]))] = number
    return numbers


@pytest.mark.parametrize(
    ("capture", "segment"),
    [("r202645-e", _HEX), ("r202639-e", None)],
    ids=["from release 202645", "before it"],
)
def test_a_clone_records_the_numbers_its_panel_publishes(
    capture: str, segment: str | None
) -> None:
    config = _cloned(capture)

    numbers = _cloned_numbers(config)
    assert len(numbers) == 46
    assert numbers == _tree_numbers("r202645-e")
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    assert panel.get("device_id_hex") == segment
    validate_yaml_config(config)


def test_a_main_32_clone_records_no_numbers() -> None:
    config = _cloned("main32_r202645")

    assert not _cloned_numbers(config)
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    assert "device_id_hex" not in panel


def test_a_clone_records_its_remote_ct_s_number() -> None:
    config = _cloned("r202645-a")

    meters = config["outside_meters"]
    assert isinstance(meters, list)
    assert [m.get("circuit_number") for m in meters] == [3]
    validate_yaml_config(config)


def test_an_import_records_no_numbers() -> None:
    """A definition's ids are masked and renumbered in space order: no panel's."""
    config = config_from_definition(load_reference_capture("r202645-e").definition)

    assert not _cloned_numbers(config)
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    assert "device_id_hex" not in panel


def _store_with_a_free_space(tmp_path: Path, config: dict[str, object]) -> ConfigStore:
    """*config* in a dashboard store, one circuit on a space of its own deleted, since
    an MLO 48's tandems fill all its spaces."""
    store = ConfigStore()
    store.load_from_file(write_config(tmp_path / "panel.yaml", config))
    (lone, *_) = [
        c for c in _circuits(config) if len(c.get("tabs") or []) == 1 and not c.get("shared_with")
    ]
    store.delete_entity(str(lone["id"]))
    assert store.get_unmapped_tabs()
    return store


def _exported(store: ConfigStore) -> dict[str, object]:
    exported: dict[str, object] = yaml.safe_load(store.export_yaml())
    return exported


def _lowest_unused(config: dict[str, object]) -> int:
    used = {c["circuit_number"] for c in _circuits(config) if "circuit_number" in c}
    return min(set(range(1, len(used) + 2)) - used)


@pytest.mark.parametrize("how", ["circuit", "from spaces"])
def test_a_circuit_added_to_a_numbered_panel_takes_the_lowest_unused_number(
    tmp_path: Path, how: str
) -> None:
    store = _store_with_a_free_space(tmp_path, reference_template("r202645-e"))
    expected = _lowest_unused(_exported(store))
    free = store.get_unmapped_tabs()

    added = (
        store.add_entity("circuit") if how == "circuit" else store.add_entity_from_tabs([free[0]])
    )

    exported = _exported(store)
    (circuit,) = [c for c in _circuits(exported) if c["id"] == added.id]
    assert circuit["circuit_number"] == expected
    validate_yaml_config(exported)


def test_a_circuit_added_to_an_unnumbered_panel_takes_none(tmp_path: Path) -> None:
    store = _store_with_a_free_space(tmp_path, _raw(_MLO_48))

    added = store.add_entity_from_tabs([store.get_unmapped_tabs()[0]])

    (circuit,) = [c for c in _circuits(_exported(store)) if c["id"] == added.id]
    assert "circuit_number" not in circuit
