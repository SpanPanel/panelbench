"""Load and parse vendored Homie 5 device profile JSONs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from span_panel_simulator.flat_emitter.exceptions import ProfileValidationError

_DEFAULT_DIR = Path(__file__).parent / "profiles"


@dataclass(frozen=True, slots=True)
class ProfileProperty:
    name: str
    datatype: str
    unit: str | None
    format: str | None
    settable: bool


@dataclass(frozen=True, slots=True)
class ProfileCapability:
    type: str
    properties: dict[str, ProfileProperty]


@dataclass(frozen=True, slots=True)
class Profile:
    entity_class: str
    version: int
    type: str
    capabilities: dict[str, ProfileCapability]

    def settable_properties(self) -> list[tuple[str, str]]:
        """Return [(capability, property_key), ...] for every settable=True property."""
        out: list[tuple[str, str]] = []
        for cap_name, cap in self.capabilities.items():
            for prop_key, prop in cap.properties.items():
                if prop.settable:
                    out.append((cap_name, prop_key))
        return out


class ProfileTable(dict[str, Profile]):
    """Mapping of entity_class → Profile."""


def load_profiles(directory: Path = _DEFAULT_DIR) -> ProfileTable:
    table = ProfileTable()
    for path in sorted(directory.glob("*.json")):
        entity_class = path.stem
        raw = json.loads(path.read_text())
        if "$version" not in raw or "type" not in raw or "capabilities" not in raw:
            raise ProfileValidationError(f"profile {path} missing required top-level keys")
        capabilities = {
            cap_name: ProfileCapability(
                type=cap["type"],
                properties={
                    prop_key: ProfileProperty(
                        name=prop["name"],
                        datatype=prop["datatype"],
                        unit=prop.get("unit"),
                        format=prop.get("format"),
                        settable=prop.get("settable", False),
                    )
                    for prop_key, prop in cap["properties"].items()
                },
            )
            for cap_name, cap in raw["capabilities"].items()
        }
        table[entity_class] = Profile(
            entity_class=entity_class,
            version=raw["$version"],
            type=raw["type"],
            capabilities=capabilities,
        )
    return table
