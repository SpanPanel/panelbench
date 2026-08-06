"""Smoke test for the SDK seam — exercises real ebus_sdk Property construction."""

from __future__ import annotations

import ebus_sdk
import pytest

from span_panel_simulator.ebus_emitter.wire._sdk_seam import make_property, set_property_value


def test_make_property_attaches_to_node() -> None:
    device = ebus_sdk.Device("d1", name="Test")
    node = device.add_node_from_dict({"id": "meter", "name": "Meter", "type": "meter"})
    prop = make_property(
        node=node,
        key="active-power",
        name="Active Power",
        datatype=ebus_sdk.PropertyDatatype.FLOAT,
        unit=ebus_sdk.Unit.WATT,
        format_str=None,
        settable=False,
    )
    assert prop is not None
    assert prop.id() == "active-power"


@pytest.mark.asyncio
async def test_set_property_value_does_not_raise() -> None:
    device = ebus_sdk.Device("d1", name="Test")
    node = device.add_node_from_dict({"id": "meter", "name": "Meter", "type": "meter"})
    prop = make_property(
        node=node,
        key="active-power",
        name="Active Power",
        datatype=ebus_sdk.PropertyDatatype.FLOAT,
        unit=ebus_sdk.Unit.WATT,
        format_str=None,
        settable=False,
    )
    # Without an attached MQTT client this is a no-op; we just want the seam to not raise.
    await set_property_value(prop, 1234.5)


def test_unmodelled_unit_reaches_the_wire_as_a_string() -> None:
    """A legitimate unit the SDK enum lacks must publish, not vanish.

    Homie's unit is free-form and the SDK accepts a plain string, so dropping a unit the
    `Unit` enum happens not to model was our bug rather than an SDK limit. The spec's own
    breaker catalog uses `kA`, which the enum lacks.
    """
    import ebus_sdk

    from span_panel_simulator.ebus_emitter.wire.graph_builder import _to_sdk_unit

    assert _to_sdk_unit("kA") == "kA"
    assert _to_sdk_unit("V") is ebus_sdk.Unit("V")
    assert _to_sdk_unit(None) is None

    device = ebus_sdk.Device("d1", name="D", type="t")
    node = device.add_node(ebus_sdk.Node("n1", name="N", type="energy.ebus.capability.breaker"))
    prop = make_property(
        node=node,
        key="interrupting-rating",
        name="Interrupting rating",
        datatype=ebus_sdk.PropertyDatatype("float"),
        unit=_to_sdk_unit("kA"),
        format_str=None,
        settable=False,
    )
    assert prop.description()["unit"] == "kA"
