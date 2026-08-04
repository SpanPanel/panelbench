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
