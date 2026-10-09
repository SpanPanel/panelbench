"""The fidelity bar's own rules, on trees small enough to read."""

from __future__ import annotations

from ebus_panel_sim.capture import Device, Tree

from .tree_diff import device_differences, differences, roles

_PANEL = "energy.ebus.device.distribution-enclosure"
_CIRCUIT = "energy.ebus.device.circuit"


def _nodes(declarations: dict[str, dict[str, object]]) -> dict[str, object]:
    """``$description`` nodes declaring each ``node/property`` path of *declarations*."""
    nodes: dict[str, dict[str, dict[str, object]]] = {}
    for path, declaration in declarations.items():
        node, _, prop = path.partition("/")
        nodes.setdefault(node, {"properties": {}})["properties"][prop] = declaration
    return dict(nodes)


def _circuit(spaces: str | None, name: str, power: str = "-5.0") -> Device:
    """A circuit device, or with no *spaces* a meter outside the panel."""
    declarations: dict[str, dict[str, object]] = {
        "meter/active-power": {"datatype": "float", "unit": "W"},
    }
    values = {"meter/active-power": power}
    if spaces is not None:
        declarations["info/spaces"] = {"datatype": "string"}
        declarations["info/name"] = {"datatype": "string"}
        values |= {"info/spaces": spaces, "info/name": name}
    return Device(description={"type": _CIRCUIT, "nodes": _nodes(declarations)}, properties=values)


def test_a_commissioned_value_is_compared_exactly_and_a_reading_by_shape() -> None:
    """Same shape, different values: a commissioned limit differs, a reading does not."""
    declared = {"datatype": "float", "unit": "A"}
    description = {
        "type": _PANEL,
        "nodes": {
            "pcs": {"properties": {"off-grid-import-limit": declared}},
            "meter": {"properties": {"voltage-a": declared}},
        },
    }

    def device(limit: str, voltage: str) -> Device:
        return Device(
            description=description,
            properties={"pcs/off-grid-import-limit": limit, "meter/voltage-a": voltage},
        )

    assert device_differences(device("47.9", "121.7"), device("48.0", "122.0")) == [
        "pcs/off-grid-import-limit value: 47.9 vs 48.0"
    ]


def test_a_value_in_another_literal_form_differs() -> None:
    """An integer where the panel publishes one decimal is a difference; so is -0.0."""
    captured = _circuit("1", "Lights", power="-5.0")

    assert device_differences(captured, _circuit("1", "Lights", power="-5")) == [
        "meter/active-power shape: 1 decimal places (-5.0) vs integer (-5)"
    ]
    assert device_differences(
        _circuit("1", "Lights", power="0.0"), _circuit("1", "Lights", "-0.0")
    )


def test_a_power_of_the_other_sign_differs_only_from_a_watt() -> None:
    captured = _circuit("1", "Lights", power="-5.0")

    assert device_differences(captured, _circuit("1", "Lights", power="5.0")) == [
        "meter/active-power sign: -5.0 vs 5.0"
    ]
    assert (
        device_differences(_circuit("1", "Lights", "-0.4"), _circuit("1", "Lights", "0.4")) == []
    )


def test_circuits_sharing_a_space_align_by_name_and_a_meter_outside_the_panel_by_ordinal() -> None:
    tree: Tree = {
        "a": _circuit("44", "Dryer"),
        "b": _circuit("44", "Washer"),
        "m": _circuit(None, ""),
    }

    assert roles(tree) == {
        "circuit 44 Dryer": "a",
        "circuit 44 Washer": "b",
        "meter outside the panel #1": "m",
    }


def test_devices_align_by_role_whatever_their_ids() -> None:
    captured: Tree = {"masked-1": _circuit("3", "Pump")}
    published: Tree = {"9f0c": _circuit("3", "Pump")}

    assert differences(captured, published) == []
    assert differences(captured, {}) == ["circuit 3 Pump: only in the capture"]
