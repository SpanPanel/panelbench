"""Homie 5 $description documents parsed into a typed tree.

Knows the Homie document shape and nothing else: no eBus vocabulary, no catalogs,
no transport. Everything downstream reads this model rather than raw JSON.
"""

from __future__ import annotations

from dataclasses import dataclass


class DescriptionError(ValueError):
    """A $description document that cannot be parsed into the model.

    Raised rather than skipped: a checker that quietly validates nothing is worse
    than no checker.
    """


@dataclass(frozen=True)
class HomieProperty:
    id: str
    name: str
    datatype: str
    unit: str | None
    format: str | None
    settable: bool
    retained: bool


@dataclass(frozen=True)
class HomieNode:
    id: str
    name: str
    type: str | None
    properties: dict[str, HomieProperty]


@dataclass(frozen=True)
class HomieDevice:
    id: str
    name: str
    type: str | None
    nodes: dict[str, HomieNode]
    children: tuple[str, ...]
    root: str | None
    parent: str | None


@dataclass(frozen=True)
class HomieTree:
    devices: dict[str, HomieDevice]


def _as_dict(value: object, what: str) -> dict[str, object]:
    """Narrow an arbitrary JSON value to a string-keyed mapping.

    ``json.loads`` is typed ``Any``; binding through ``object`` and narrowing here is
    what keeps ``Any`` out of the package under mypy --strict.
    """
    if not isinstance(value, dict):
        raise DescriptionError(f"{what}: expected an object, got {type(value).__name__}")
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise DescriptionError(f"{what}: non-string key {key!r}")
        narrowed[key] = item
    return narrowed


def _as_str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise DescriptionError(f"{what}: expected a string, got {type(value).__name__}")
    return value


def _opt_str(raw: dict[str, object], key: str, what: str) -> str | None:
    if key not in raw:
        return None
    return _as_str(raw[key], f"{what}.{key}")


def _parse_property(prop_id: str, value: object, what: str) -> HomieProperty:
    raw = _as_dict(value, what)
    if "datatype" not in raw:
        raise DescriptionError(f"{what}: missing datatype")
    # Homie omits these keys rather than publishing null, so absence is the default.
    # Note the inversion on `retained`: absent means retained.
    return HomieProperty(
        id=prop_id,
        name=_opt_str(raw, "name", what) or prop_id,
        datatype=_as_str(raw["datatype"], f"{what}.datatype"),
        unit=_opt_str(raw, "unit", what),
        format=_opt_str(raw, "format", what),
        settable=raw.get("settable") is True,
        retained=raw.get("retained") is not False,
    )


def _parse_node(node_id: str, value: object, what: str) -> HomieNode:
    raw = _as_dict(value, what)
    properties_raw = _as_dict(raw.get("properties", {}), f"{what}.properties")
    properties = {
        prop_id: _parse_property(prop_id, prop, f"{what}.properties.{prop_id}")
        for prop_id, prop in properties_raw.items()
    }
    return HomieNode(
        id=node_id,
        name=_opt_str(raw, "name", what) or node_id,
        type=_opt_str(raw, "type", what),
        properties=properties,
    )


def parse_device(device_id: str, value: object) -> HomieDevice:
    """Parse one device's $description document."""
    what = f"device {device_id}"
    raw = _as_dict(value, what)
    nodes_raw = _as_dict(raw.get("nodes", {}), f"{what}.nodes")
    nodes = {
        node_id: _parse_node(node_id, node, f"{what}.nodes.{node_id}")
        for node_id, node in nodes_raw.items()
    }
    children_raw = raw.get("children", [])
    if not isinstance(children_raw, list):
        raise DescriptionError(f"{what}.children: expected a list")
    children = tuple(_as_str(child, f"{what}.children[]") for child in children_raw)
    return HomieDevice(
        id=device_id,
        name=_opt_str(raw, "name", what) or device_id,
        type=_opt_str(raw, "type", what),
        nodes=nodes,
        children=children,
        root=_opt_str(raw, "root", what),
        parent=_opt_str(raw, "parent", what),
    )


def build_tree(documents: dict[str, object]) -> HomieTree:
    """Parse a mapping of device id to $description document."""
    return HomieTree(
        devices={device_id: parse_device(device_id, raw) for device_id, raw in documents.items()}
    )
