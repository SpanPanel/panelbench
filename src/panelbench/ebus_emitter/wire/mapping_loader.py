"""Load and parse vendored mapping descriptor YAMLs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

from panelbench.ebus_emitter.exceptions import ProfileValidationError
from panelbench.ebus_emitter.wire.profile_loader import ProfileTable

_DEFAULT_DIR = Path(__file__).parent / "mapping"

PlacementKind = Literal["root-device", "node-on-parent", "child-of-parent"]


@dataclass(frozen=True, slots=True)
class Placement:
    kind: PlacementKind
    parent_entity_class: str | None = None
    node_id_template: str | None = None
    device_id_template: str | None = None


@dataclass(frozen=True, slots=True)
class WireConfig:
    device_id_source: Literal["self", "parent"]
    property_path_template: str


@dataclass(frozen=True, slots=True)
class DisplayConfig:
    name_template: str
    fallback_name_template: str


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    description_owner: Literal["self", "parent"]
    state_owner: Literal["self", "parent"]
    parent_back_reference: str | None = None


@dataclass(frozen=True, slots=True)
class MappingDescriptor:
    entity_class: str
    profile: str
    profile_version: int
    placement: Placement
    wire: WireConfig
    display: DisplayConfig
    discovery: DiscoveryConfig


class MappingTable(dict[str, MappingDescriptor]):
    """Mapping of entity_class → MappingDescriptor."""

    def root_entity_class(self) -> str:
        """Return the entity_class whose placement is the root device.

        Raises ``ProfileValidationError`` if the table does not contain
        exactly one root-device descriptor (also enforced by
        ``validate_against`` at load time)."""
        roots = [m.entity_class for m in self.values() if m.placement.kind == "root-device"]
        if len(roots) != 1:
            raise ProfileValidationError(
                f"mapping table must have exactly one root-device descriptor; got {len(roots)}"
            )
        return roots[0]

    def validate_against(self, profiles: ProfileTable) -> None:
        roots = [m for m in self.values() if m.placement.kind == "root-device"]
        if len(roots) != 1:
            raise ProfileValidationError(
                f"mapping table must have exactly one root-device descriptor; got {len(roots)}"
            )
        for ec, m in self.items():
            if ec not in profiles:
                raise ProfileValidationError(
                    f"mapping {ec} references missing profile {m.profile}"
                )
            if profiles[ec].version != m.profile_version:
                raise ProfileValidationError(
                    f"mapping {ec} expects profile_version {m.profile_version}, "
                    f"profile is {profiles[ec].version}"
                )
            if (
                m.placement.parent_entity_class is not None
                and m.placement.parent_entity_class not in self
            ):
                raise ProfileValidationError(
                    f"mapping {ec} references unknown parent_entity_class "
                    f"{m.placement.parent_entity_class!r}"
                )
            if (
                m.discovery.parent_back_reference is not None
                and m.discovery.parent_back_reference not in self
            ):
                raise ProfileValidationError(
                    f"mapping {ec} references unknown parent_back_reference "
                    f"{m.discovery.parent_back_reference!r}"
                )


def load_mapping_table(directory: Path = _DEFAULT_DIR) -> MappingTable:
    table = MappingTable()
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        table[raw["entity_class"]] = MappingDescriptor(
            entity_class=raw["entity_class"],
            profile=raw["profile"],
            profile_version=raw["profile_version"],
            placement=Placement(
                kind=cast("PlacementKind", raw["placement"]["kind"]),
                parent_entity_class=raw["placement"].get("parent_entity_class"),
                node_id_template=raw["placement"].get("node_id_template"),
                device_id_template=raw["placement"].get("device_id_template"),
            ),
            wire=WireConfig(
                device_id_source=cast(
                    "Literal['self', 'parent']",
                    raw["wire"]["device_id_source"],
                ),
                property_path_template=raw["wire"]["property_path_template"],
            ),
            display=DisplayConfig(
                name_template=raw["display"]["name_template"],
                fallback_name_template=raw["display"]["fallback_name_template"],
            ),
            discovery=DiscoveryConfig(
                description_owner=cast(
                    "Literal['self', 'parent']",
                    raw["discovery"]["$description_owner"],
                ),
                state_owner=cast(
                    "Literal['self', 'parent']",
                    raw["discovery"]["state_owner"],
                ),
                parent_back_reference=raw["discovery"].get("parent_back_reference"),
            ),
        )
    return table
