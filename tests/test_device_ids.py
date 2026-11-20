"""PanelBench publishes the device ids a real panel does, by hardware and release.

A hardware 3.0 panel names its circuits by SPAN's number for each, ``<n>``, which it
keeps across releases while the id around it changes at release 202645:
``circuit-<n>`` before it, ``{serial}-{hex16}-1-16-<n>`` from it, ``remote-ct-<n>``
and ``…-1-42-<n>`` for a meter outside the panel. A config records the numbers and
the panel's 16-hex segment, and the ids follow the release, as a real panel's do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
import yaml

from panelbench.device_numbers import hex_of, next_number, number_of
from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS

if TYPE_CHECKING:
    from pathlib import Path

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
