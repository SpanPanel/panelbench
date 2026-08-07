"""Default entity values by type.

When a user adds a new entity via the dashboard, these defaults
populate the template and circuit definition.
"""

from __future__ import annotations

import re
from typing import Any

# Moved to core: config loading needs these too, and the engine importing them
# from the dashboard package would invert the layering. Re-exported here so
# existing dashboard callers keep their import site.
from panelbench.config_defaults import ENTITY_TYPE_DEFAULTS


def _slugify(name: str) -> str:
    """Convert a human name to a YAML-safe snake_case id."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def default_name_for_type(entity_type: str) -> str:
    """Return a sensible default display name for a new entity."""
    return {
        "circuit": "New Circuit",
        "pv": "Solar Inverter",
        "evse": "SPAN Drive",
    }.get(entity_type, "New Entity")


def make_defaults(
    entity_type: str, name: str | None = None
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Return ``(entity_id, template_name, template_dict, circuit_dict)``.

    The caller can insert these directly into the config store.
    """
    if entity_type not in ENTITY_TYPE_DEFAULTS:
        raise ValueError(f"Unknown entity type: {entity_type}")

    display_name = name or default_name_for_type(entity_type)
    entity_id = _slugify(display_name)
    template_name = f"{entity_id}_tpl"

    spec = ENTITY_TYPE_DEFAULTS[entity_type]
    template_dict: dict[str, Any] = dict(spec["template"])
    circuit_dict: dict[str, Any] = {
        "id": entity_id,
        "name": display_name,
        "template": template_name,
        **spec["circuit"],
    }

    return entity_id, template_name, template_dict, circuit_dict
