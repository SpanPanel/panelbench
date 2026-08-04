"""Internal seam over ebus_sdk.property.

Localises every property-construction and property-mutation call so that future SDK
changes to property.py touch one file. NOT an abstraction layer — other modules pass
ebus_sdk.Property instances around directly. The seam only owns construction and
mutation.
"""

from __future__ import annotations

from typing import Any

import ebus_sdk
from ebus_sdk import PropertyDatatype, Unit


def make_property(
    *,
    node: ebus_sdk.Node,
    key: str,
    name: str,
    datatype: PropertyDatatype,
    unit: Unit | None,
    format_str: str | None,
    settable: bool,
) -> ebus_sdk.Property:
    """Construct an ebus_sdk.Property and attach it to a node."""
    spec: dict[str, Any] = {
        "id": key,
        "name": name,
        "datatype": datatype,
    }
    if unit is not None:
        spec["unit"] = unit
    if format_str is not None:
        spec["format"] = format_str
    if settable:
        spec["settable"] = True
    return node.add_property_from_dict(spec)


async def set_property_value(prop: ebus_sdk.Property, value: object) -> None:
    """Set a property value. Async-only signature — forward-compat hedge against any
    future SDK change to make set_value async."""
    # `set_value` unconditionally: the pin is exact, so the capability is known rather
    # than probed. The old fallback assigned to `coerced_value`, which is a zero-arg
    # getter — that would have replaced the method with a value rather than setting
    # one. Dead in practice (the SDK has always had `set_value`) and wrong if reached,
    # so it goes rather than being corrected.
    prop.set_value(value)


def settable_handler_signature(prop: ebus_sdk.Property) -> tuple[type, ...]:
    """SDK introspection used by set_router for handler validation. The current SDK
    delivers /set values as strings; set_router decodes per profile datatype."""
    _ = prop
    return (str,)
