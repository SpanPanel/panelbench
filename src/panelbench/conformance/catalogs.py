"""Vendored eBus capability catalogs, loaded as data.

Reads the catalog JSON the emitter ships, without importing anything
from the emitter: the checker must be able to validate a tree it did not build.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Units that name a *dimension* rather than a unit. A publisher MUST substitute a
# concrete unit before publishing; the token is never valid on the wire.
#
# This set is the one piece of catalog semantics that does NOT travel with the vendored
# JSON: upstream declares it in conventions/property-json.md and again in ABSTRACT_UNITS
# in the specification's tools/check-property-catalogs.py, neither of which a downstream
# copies. Until it becomes a vendorable artifact we carry it here, and
# test_every_vendored_unit_is_classified fails on the next re-vendor if upstream adds one.
ABSTRACT_UNITS: frozenset[str] = frozenset({"energy"})


class CatalogError(ValueError):
    """A capability catalog that cannot be loaded."""


@dataclass(frozen=True)
class CatalogProperty:
    id: str
    datatype: str
    unit: str | None
    req: str
    format: str | None
    settable: bool


@dataclass(frozen=True)
class Catalog:
    capability: str
    version: str
    properties: dict[str, CatalogProperty]


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogError(f"{what}: expected an object, got {type(value).__name__}")
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise CatalogError(f"{what}: non-string key {key!r}")
        narrowed[key] = item
    return narrowed


def _as_str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise CatalogError(f"{what}: expected a string, got {type(value).__name__}")
    return value


def _opt_str(raw: dict[str, object], key: str, what: str) -> str | None:
    if key not in raw:
        return None
    return _as_str(raw[key], f"{what}.{key}")


def _parse_property(prop_id: str, value: object, what: str) -> CatalogProperty:
    raw = _as_dict(value, what)
    if "datatype" not in raw:
        raise CatalogError(f"{what}: missing datatype")
    return CatalogProperty(
        id=prop_id,
        datatype=_as_str(raw["datatype"], f"{what}.datatype"),
        unit=_opt_str(raw, "unit", what),
        req=_opt_str(raw, "req", what) or "MAY",
        format=_opt_str(raw, "format", what),
        settable=raw.get("settable") is True,
    )


def _load_one(path: Path) -> Catalog:
    parsed: object = json.loads(path.read_text())
    raw = _as_dict(parsed, str(path))
    capability = _opt_str(raw, "capability", str(path))
    if capability is None:
        raise CatalogError(f"{path}: no 'capability' field; not a capability catalog")
    properties_raw = _as_dict(raw.get("properties", {}), f"{path}.properties")
    properties = {
        prop_id: _parse_property(prop_id, prop, f"{path}:{prop_id}")
        for prop_id, prop in properties_raw.items()
    }
    return Catalog(
        capability=capability,
        version=_opt_str(raw, "version", str(path)) or "unknown",
        properties=properties,
    )


def load_catalogs(directory: Path) -> dict[str, Catalog]:
    """Load every capability catalog in *directory*, keyed by capability type."""
    catalogs: dict[str, Catalog] = {}
    for path in sorted(directory.glob("*.json")):
        catalog = _load_one(path)
        catalogs[catalog.capability] = catalog
    if not catalogs:
        raise CatalogError(f"no capability catalogs found in {directory}")
    return catalogs
