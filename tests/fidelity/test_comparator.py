"""The comparator's own premises, on synthetic captures."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from .against_spec import declared_but_unvalued, device_id_findings
from .comparator import Capture, ParityReport, by_role, compare, compare_settable

_CIRCUIT = "energy.ebus.device.circuit"
_COMMISSIONED_PV = f"{_CIRCUIT}::Commissioned PV System"


def _circuit(spaces: str, *, priority_settable: bool) -> dict[str, str]:
    """A circuit named as SPAN names a commissioned PV system's, at *spaces*."""
    priority: dict[str, object] = {"name": "priority", "datatype": "enum"}
    if priority_settable:
        priority["settable"] = True
    description = {
        "type": _CIRCUIT,
        "name": "Commissioned PV System",
        "nodes": {
            "info": {"properties": {"spaces": {"name": "spaces", "datatype": "string"}}},
            "load-shed": {"properties": {"priority": priority}},
        },
    }
    return {
        "$description": json.dumps(description),
        "info/spaces": spaces,
        "load-shed/priority": "NEVER",
    }


def _device(device_class: str, name: str, **properties: str) -> dict[str, str]:
    """A device of *device_class* named *name*, publishing *properties*."""
    description: dict[str, object] = {"type": f"energy.ebus.device.{device_class}", "name": name}
    parent = properties.pop("parent", None)
    if parent is not None:
        description["parent"] = parent
    return {"$description": json.dumps(description), **properties}


def _feeding(spaces: str, device_id: str) -> dict[str, str]:
    """A circuit at *spaces* whose connection names *device_id* as what it feeds."""
    return {
        **_device("circuit", "Commissioned PV System", **{"info/spaces": spaces}),
        "connection/feeds-device-id": device_id,
    }


def _upstream_battery(battery: str, battery_name: str, mid: str, mid_name: str) -> Capture:
    """A battery feeding the upstream lugs, and its MID, under the given ids and names."""
    return {
        "lugs-up": _device(
            "lugs",
            "Upstream lugs",
            **{"info/direction": "UPSTREAM", "connection/fed-by-device-id": battery},
        ),
        battery: _device("bess", battery_name),
        mid: _device("mid", mid_name, parent=battery),
    }


def test_devices_sharing_a_role_stay_distinct() -> None:
    both = {
        "a": _circuit("36,38", priority_settable=False),
        "b": _circuit("31,33", priority_settable=False),
    }

    one = {"a": both["a"]}

    assert len(by_role(both)) == 2
    report = compare(both, one)
    assert report.missing_devices == {f"{_COMMISSIONED_PV} @31,33": 3}
    assert report.extra_devices == {}, "the circuit both sides publish keys the same on both"


def test_any_other_repeated_role_is_refused() -> None:
    lugs = _device("lugs", "Upstream lugs")

    with pytest.raises(ValueError, match="share the role"):
        by_role({"a": lugs, "b": lugs})


def test_an_inverter_aligns_by_the_circuit_feeding_it_whatever_it_is_named() -> None:
    """SPAN firmware names an inverter after its own device id, which the two
    producers derive differently, so only where it hangs can align it."""
    firmware = {
        "c1": _feeding("29,31", "nt-0000-iq7"),
        "nt-0000-iq7": _device("pv", "nt-0000-iq7"),
    }
    simulator = {"c1": _feeding("29,31", "sim-pv-1"), "sim-pv-1": _device("pv", "Solar")}

    assert set(by_role(firmware)) == set(by_role(simulator))
    assert "energy.ebus.device.pv @29,31" in by_role(simulator)
    assert compare(firmware, simulator).as_baseline() == ParityReport().as_baseline()


def test_a_battery_and_its_mid_align_by_the_lugs_the_battery_feeds() -> None:
    firmware = _upstream_battery("nt-0000-tg1", "nt-0000-tg1", "nt-0000-tg2", "nt-0000-tg2")
    simulator = _upstream_battery(
        "sim-bess", "Battery", "sim-bess-mid", "Microgrid Interconnect Device"
    )

    assert set(by_role(simulator)) == {
        "energy.ebus.device.lugs::Upstream lugs",
        "energy.ebus.device.bess @UPSTREAM lugs",
        "energy.ebus.device.mid of energy.ebus.device.bess @UPSTREAM lugs",
    }
    assert compare(firmware, simulator).as_baseline() == ParityReport().as_baseline()


@pytest.mark.parametrize(
    "devices",
    [
        {"pv": _device("pv", "Solar")},
        {"pv": _device("pv", "Solar"), "c1": _feeding("1", "pv"), "c2": _feeding("2", "pv")},
        {"mid": _device("mid", "Microgrid Interconnect Device", parent="missing")},
        {"mid": _device("mid", "Microgrid Interconnect Device")},
    ],
    ids=["unfed inverter", "inverter fed twice", "mid of an absent battery", "mid of no battery"],
)
def test_a_device_whose_place_cannot_be_resolved_is_refused(devices: Capture) -> None:
    """Never keyed by its name instead, which would bring back the misalignment quietly."""
    with pytest.raises(ValueError, match="cannot be keyed by topology"):
        by_role(devices)


@pytest.mark.parametrize(
    "check", [declared_but_unvalued, device_id_findings], ids=lambda check: check.__name__
)
def test_the_absolute_checks_refuse_a_repeated_role_too(
    check: Callable[[dict[str, dict[str, str]]], object],
) -> None:
    """The checks against the specification key by role as the comparisons do, so a
    repeat would let one device's findings stand for both."""
    lugs = _device("lugs", "Upstream lugs")
    devices = {
        "sim-0001": _device(
            "distribution-enclosure", "Panel", **{"info/serial-number": "sim-0001"}
        ),
        "a": lugs,
        "b": lugs,
    }

    with pytest.raises(ValueError, match="share the role"):
        check(devices)


def test_a_lost_lock_is_a_settable_difference() -> None:
    locked = {"a": _circuit("36,38", priority_settable=False)}
    unlocked = {"a": _circuit("36,38", priority_settable=True)}

    assert compare(locked, unlocked).as_baseline() == compare(locked, locked).as_baseline()
    assert compare_settable(locked, unlocked) == {
        f"{_COMMISSIONED_PV} @36,38": {
            "reference_only": [],
            "panelbench_only": ["load-shed/priority"],
        }
    }
