"""Per-tick property values + diff cache."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

PropertyKey = tuple[str, str, str]  # (entity_class, instance_id, property_path)


@dataclass(slots=True)
class PropertyBag:
    values: dict[PropertyKey, object]

    def set(
        self,
        entity_class: str,
        instance_id: str,
        property_path: str,
        value: object,
    ) -> None:
        self.values[(entity_class, instance_id, property_path)] = value

    def get(self, key: PropertyKey) -> object | None:
        return self.values.get(key)

    def __len__(self) -> int:
        return len(self.values)


@dataclass(slots=True)
class PropertyDiffer:
    all_keys: tuple[PropertyKey, ...] = field(default_factory=tuple)
    last_published: dict[PropertyKey, object] = field(default_factory=dict)
    pending_initial: set[PropertyKey] = field(default_factory=set)

    def __init__(self, all_keys: Iterable[PropertyKey]) -> None:
        self.all_keys = tuple(all_keys)
        self.last_published = {}
        self.pending_initial = set(self.all_keys)

    def diff(self, bag: PropertyBag) -> list[tuple[PropertyKey, object]]:
        changes: list[tuple[PropertyKey, object]] = []
        for key in self.all_keys:
            if key not in bag.values:
                continue
            value = bag.values[key]
            if key in self.pending_initial or self.last_published.get(key) != value:
                changes.append((key, value))
        changes.sort(key=lambda kv: kv[0])
        return changes

    def commit(self, published: list[tuple[PropertyKey, object]]) -> None:
        for key, value in published:
            self.last_published[key] = value
            self.pending_initial.discard(key)
