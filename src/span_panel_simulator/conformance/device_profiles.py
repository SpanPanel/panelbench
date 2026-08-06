"""Vendored eBus device profiles: which capabilities a device type composes.

Distinct from the *conformance report* this package produces. A device profile is
upstream's data; the report is our output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ProfileError(ValueError):
    """A device profile that cannot be loaded."""


@dataclass(frozen=True)
class DeviceProfile:
    device_type: str
    role: str | None
    capabilities: dict[str, str]
    """Node id to the capability type it implements."""


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProfileError(f"{what}: expected an object, got {type(value).__name__}")
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ProfileError(f"{what}: non-string key {key!r}")
        narrowed[key] = item
    return narrowed


def _opt_str(raw: dict[str, object], key: str, what: str) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise ProfileError(f"{what}.{key}: expected a string")
    return value


def load_device_profiles(directory: Path) -> dict[str, DeviceProfile]:
    """Load every device profile in *directory*, keyed by device type.

    Sub-directories are not searched: the SPAN overlay lives under ``profiles/span`` and
    is applied by the emitter, not by the checker, which compares against base catalogs.
    """
    profiles: dict[str, DeviceProfile] = {}
    for path in sorted(directory.glob("*.json")):
        parsed: object = json.loads(path.read_text())
        raw = _as_dict(parsed, str(path))
        device_types = _as_dict(raw.get("device_types", {}), f"{path}.device_types")
        for device_type, entry_raw in device_types.items():
            entry = _as_dict(entry_raw, f"{path}.device_types.{device_type}")
            capabilities_raw = _as_dict(
                entry.get("capabilities", {}), f"{path}.{device_type}.capabilities"
            )
            capabilities: dict[str, str] = {}
            for node_id, ref_raw in capabilities_raw.items():
                ref = _as_dict(ref_raw, f"{path}.{device_type}.capabilities.{node_id}")
                catalog = _opt_str(ref, "catalog", f"{path}.{device_type}.{node_id}")
                if catalog is None:
                    raise ProfileError(
                        f"{path}: {device_type}.{node_id} has no 'catalog' reference"
                    )
                capabilities[node_id] = catalog
            profiles[device_type] = DeviceProfile(
                device_type=device_type,
                role=_opt_str(entry, "role", f"{path}.{device_type}"),
                capabilities=capabilities,
            )
    return profiles
