"""The comparator's own premises, on synthetic captures."""

from __future__ import annotations

import json

import pytest

from .comparator import by_role, compare, compare_settable

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
    solar = json.dumps({"type": "energy.ebus.device.pv", "name": "Solar"})

    with pytest.raises(ValueError, match="share the role"):
        by_role({"a": {"$description": solar}, "b": {"$description": solar}})


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
