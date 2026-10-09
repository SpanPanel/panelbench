"""The fidelity bar: what a published tree must share with the panel it reproduces.

A tree reproduces a panel when it declares what the panel declares and values what
the panel values, each value in the panel's literal form. Device by device that is
the ``$description`` keys, type and children; the nodes; each declared property
and its datatype, settable, unit, format and name; whether each property is valued;
the shape of each value, an integer or the same number of decimal places, never a
negative zero; the value itself where it is commissioning rather than a reading
(``COMPARED_BY_VALUE``); and the sign of each power of a watt or more.

Three things may differ, because they differ between any two moments of one panel
or between any panel and its masked capture: time-varying magnitudes, masked
identifiers, and ``connection/count``, which nothing models.

Devices are aligned by role, never by id, because a reproduction's ids are its own.
A circuit by its spaces and name, a circuit without spaces (a meter outside the
panel) by its ordinal, lugs by direction, a battery, inverter or SPAN Drive by the
circuit or lugs it hangs from, a MID by its battery, and any other device by its
type's ordinal.

This is the bar the emitter's own reference-capture test holds itself to, so a
difference here that the emitter does not show is PanelBench's.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Final

from ebus_panel_sim.capture import Device, Tree, tree_from_retained

NOT_COMPARED: Final = frozenset({"connection/count"})
"""Declared by a SPAN panel and modelled by nothing: the units aggregated behind a node."""

COMPARED_BY_VALUE: Final = frozenset(
    {
        "breaker/poles",
        "breaker/rating",
        "config/max-charge-current",
        "config/user-max-charge-current",
        "connection/backed-up",
        "connection/feeds-role",
        "connection/overcurrent-protection",
        "connection/service-rating",
        "info/dedicated",
        "info/locations",
        "info/model",
        "info/nominal-voltage",
        "info/tags",
        "pcs/off-grid-import-limit",
        "pcs/off-grid-import-limit-enablement",
        "pcs/operator-import-limit-enablement",
        "pcs/priority",
    }
)
"""Commissioning a reproduction carries as it is: neither a reading nor masked."""

_ATTRIBUTES: Final = ("datatype", "settable", "unit", "format", "name")
_SIGNED: Final = re.compile(r"^(meter/active-power|power-flows/.+)$")
_NUMBER: Final = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
_FED: Final = frozenset({"bess", "evse", "pv"})


def published_tree(retained: dict[str, bytes]) -> Tree:
    """Retained topics as the tree a consumer replays from them."""
    return tree_from_retained({topic: payload.decode() for topic, payload in retained.items()})


def retained_topics(tree: Tree, domain: str = "ebus/5") -> dict[str, bytes]:
    """*tree* as the retained topics its panel publishes: the inverse of ``published_tree``."""
    retained: dict[str, bytes] = {}
    for device_id, device in tree.items():
        retained[f"{domain}/{device_id}/$description"] = json.dumps(device.description).encode()
        for path, value in device.properties.items():
            retained[f"{domain}/{device_id}/{path}"] = value.encode()
    return retained


def _mapping(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _nodes(device: Device) -> dict[str, dict[str, object]]:
    return {
        node: _mapping(body) for node, body in _mapping(device.description.get("nodes")).items()
    }


def _declarations(body: dict[str, object]) -> dict[str, dict[str, object]]:
    return {key: _mapping(decl) for key, decl in _mapping(body.get("properties")).items()}


def _meter_outside_the_panel(device: Device) -> bool:
    """A circuit device that declares no breaker space: a meter, not a branch circuit."""
    return device.type == "circuit" and not device.declares("info/spaces")


def _place(device_id: str, tree: Tree) -> str | None:
    """Where a battery, inverter or SPAN Drive hangs: its circuit's spaces, or lugs."""
    for other in tree.values():
        if other.type == "circuit" and other.value("connection/feeds-device-id") == device_id:
            return f"@{other.value('info/spaces')}"
        if other.type == "lugs" and other.value("connection/fed-by-device-id") == device_id:
            return f"@{other.value('info/direction')} lugs"
    return None


def roles(tree: Tree) -> dict[str, str]:
    """Role to device id, so no masked or producer-chosen id decides an alignment.

    A MID's role names its battery's, so every other device is placed first.
    """
    out: dict[str, str] = {}
    role_by_id: dict[str, str] = {}
    ordinals: Counter[str] = Counter()

    def ordinal(kind: str) -> str:
        ordinals[kind] += 1
        return f"{kind} #{ordinals[kind]}"

    def role_of(device_id: str) -> str:
        device = tree[device_id]
        if _meter_outside_the_panel(device):
            return ordinal("meter outside the panel")
        if device.type == "circuit":
            return f"circuit {device.value('info/spaces')} {device.value('info/name')}"
        if device.type == "lugs":
            return f"lugs {device.value('info/direction')}"
        if device.type in _FED and (place := _place(device_id, tree)) is not None:
            return f"{device.type} {place}"
        if device.type == "mid":
            parent = device.description.get("parent")
            if isinstance(parent, str) and parent in role_by_id and tree[parent].type == "bess":
                return f"mid of {role_by_id[parent]}"
        return ordinal(device.type)

    ordered = sorted(tree, key=lambda device_id: (tree[device_id].type == "mid", device_id))
    for device_id in ordered:
        role = role_of(device_id)
        if role in out:
            ordinals[role] += 1
            role = f"{role} ~{ordinals[role]}"
        out[role] = device_id
        role_by_id[device_id] = role
    return out


def shape(value: str, declaration: dict[str, object]) -> str:
    """What a value looks like on the wire, by its declared datatype."""
    datatype = declaration.get("datatype")
    if value == "":
        return "empty"
    if datatype in ("integer", "float"):
        if value.startswith("-0") and value.strip("-0.") == "":
            return "negative zero"
        if not _NUMBER.match(value):
            return f"not a number ({datatype})"
        if "e" in value.lower():
            return "exponent"
        if "." not in value:
            return "integer"
        return f"{len(value.split('.', 1)[1])} decimal places"
    if datatype == "boolean":
        return "boolean" if value in ("true", "false") else "not a boolean"
    if datatype == "enum":
        return "in format" if value in str(declaration.get("format", "")).split(",") else "out"
    if datatype == "json":
        try:
            json.loads(value)
        except ValueError:
            return "not json"
        return "json"
    return "string"


def _sign(value: str) -> int:
    """The sign of a power, or 0 when it is under a watt or not a number."""
    try:
        number = float(value)
    except ValueError:
        return 0
    return 0 if abs(number) < 1.0 else (1 if number > 0 else -1)


def _children_by_type(tree: Tree, device: Device) -> Counter[str]:
    children = device.description.get("children")
    listed = children if isinstance(children, list) else []
    return Counter(tree[c].type for c in listed if isinstance(c, str) and c in tree)


def device_differences(captured: Device, published: Device) -> list[str]:
    """Everything *published* declares or values differently from *captured*."""
    out: list[str] = []
    top_c = set(captured.description) - {"nodes"}
    top_p = set(published.description) - {"nodes"}
    if top_c != top_p:
        out.append(f"$description keys: {sorted(top_c ^ top_p)}")
    for key in ("homie", "type"):
        if captured.description.get(key) != published.description.get(key):
            out.append(f"$description {key}")
    nodes_c, nodes_p = _nodes(captured), _nodes(published)
    if set(nodes_c) != set(nodes_p):
        out.append(f"nodes: {sorted(set(nodes_c) ^ set(nodes_p))}")
    for node in sorted(set(nodes_c) & set(nodes_p)):
        if nodes_c[node].get("type") != nodes_p[node].get("type"):
            out.append(f"node {node} type")
        decl_c, decl_p = _declarations(nodes_c[node]), _declarations(nodes_p[node])
        for key in sorted(set(decl_c) ^ set(decl_p)):
            if f"{node}/{key}" not in NOT_COMPARED:
                where = "capture" if key in decl_c else "reproduction"
                out.append(f"{node}/{key}: declared only by the {where}")
        for key in sorted(set(decl_c) & set(decl_p)):
            path = f"{node}/{key}"
            if path in NOT_COMPARED:
                continue
            out.extend(
                f"{path} {attribute}: {decl_c[key].get(attribute)!r} vs "
                f"{decl_p[key].get(attribute)!r}"
                for attribute in _ATTRIBUTES
                if decl_c[key].get(attribute) != decl_p[key].get(attribute)
            )
            value_c, value_p = captured.value(path), published.value(path)
            if (value_c is None) != (value_p is None):
                where = "capture" if value_c is not None else "reproduction"
                out.append(f"{path}: valued only by the {where}")
            elif value_c is not None and value_p is not None:
                shape_c, shape_p = shape(value_c, decl_c[key]), shape(value_p, decl_p[key])
                if shape_c != shape_p:
                    out.append(f"{path} shape: {shape_c} ({value_c}) vs {shape_p} ({value_p})")
                elif path in COMPARED_BY_VALUE and value_c != value_p:
                    out.append(f"{path} value: {value_c} vs {value_p}")
                signs = (_sign(value_c), _sign(value_p))
                if _SIGNED.match(path) and all(signs) and signs[0] != signs[1]:
                    out.append(f"{path} sign: {value_c} vs {value_p}")
    return out


def differences(captured: Tree, published: Tree) -> list[str]:
    """Every way *published* falls short of reproducing *captured*, one line each.

    Each line starts with the device's role and names the path and the kind of
    difference before any value, so a line can be matched by its prefix.
    """
    roles_c, roles_p = roles(captured), roles(published)
    out = [f"{role}: only in the capture" for role in sorted(set(roles_c) - set(roles_p))]
    out += [f"{role}: only in the reproduction" for role in sorted(set(roles_p) - set(roles_c))]
    for role in sorted(set(roles_c) & set(roles_p)):
        device_c, device_p = captured[roles_c[role]], published[roles_p[role]]
        if _children_by_type(captured, device_c) != _children_by_type(published, device_p):
            out.append(f"{role}: children by type")
        out += [f"{role}: {d}" for d in device_differences(device_c, device_p)]
    return out
