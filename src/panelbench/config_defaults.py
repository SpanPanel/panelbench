"""What a circuit template means when it does not say.

``ENTITY_TYPE_DEFAULTS`` are the per-device-type energy profiles the simulator
falls back to. They live here rather than under ``dashboard/`` because they are a
domain fact -- what a circuit, a PV inverter or an EVSE typically draws -- not a
presentation one: config loading needs them, and core depending on the UI layer
to learn them would be backwards.

``normalize_circuit_templates`` applies them at load. A config that omits
``energy_profile`` is structurally complete and behaviourally inert rather than
invalid: the wire surface a template produces is fixed by ``device_type``,
``relay_behavior``, ``priority`` and ``breaker_rating``, while the energy profile
only decides what the numbers do. Requiring it to publish a well-formed tree
coupled structure to simulation parameters, and made the reference configs that
carry no behaviour -- the eBus emitter's own examples -- unloadable here.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

ENTITY_TYPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "circuit": {
        "template": {
            "energy_profile": {
                "mode": "consumer",
                "power_range": [0.0, 1800.0],
                "typical_power": 150.0,
                "power_variation": 0.3,
            },
            "relay_behavior": "controllable",
            "priority": "NEVER",
            "breaker_rating": 15,
        },
        "circuit": {
            "tabs": [],
        },
    },
    "pv": {
        "template": {
            "energy_profile": {
                "mode": "producer",
                "power_range": [-5000.0, 0.0],
                "typical_power": -3000.0,
                "power_variation": 0.25,
                "efficiency": 0.85,
                "nameplate_capacity_w": 5000.0,
            },
            "relay_behavior": "non_controllable",
            "priority": "NEVER",
            "device_type": "pv",
            "breaker_rating": 30,
        },
        "circuit": {
            "tabs": [],
        },
    },
    "evse": {
        "template": {
            "energy_profile": {
                "mode": "consumer",
                "power_range": [0.0, 11500.0],
                "typical_power": 7200.0,
                "power_variation": 0.05,
            },
            "relay_behavior": "controllable",
            "priority": "OFF_GRID",
            "device_type": "evse",
            "breaker_rating": 50,
            "smart_behavior": {
                "responds_to_grid": True,
                "max_power_reduction": 0.6,
            },
            "time_of_day_profile": {
                "enabled": True,
                "hour_factors": {
                    0: 1.0,
                    1: 1.0,
                    2: 1.0,
                    3: 1.0,
                    4: 1.0,
                    5: 1.0,
                    6: 0.0,
                    7: 0.0,
                    8: 0.0,
                    9: 0.0,
                    10: 0.0,
                    11: 0.0,
                    12: 0.0,
                    13: 0.0,
                    14: 0.0,
                    15: 0.0,
                    16: 0.0,
                    17: 0.0,
                    18: 0.0,
                    19: 0.0,
                    20: 0.0,
                    21: 0.0,
                    22: 0.0,
                    23: 0.0,
                },
            },
        },
        "circuit": {
            "tabs": [],
        },
    },
}


# Template top-level keys that belong inside ``energy_profile``. The eBus
# emitter's example configs put ``nameplate_capacity_w`` at template top level
# (its ``_pv_instance`` reads it there); this package reads it from the nested
# profile. Promoting rather than duplicating lets one config drive both.
_PROMOTABLE_TO_PROFILE = ("nameplate_capacity_w",)

_DEFAULT_DEVICE_TYPE = "circuit"


def default_energy_profile(device_type: str) -> dict[str, Any]:
    """The fallback energy profile for a template of *device_type*."""
    spec = ENTITY_TYPE_DEFAULTS.get(device_type, ENTITY_TYPE_DEFAULTS[_DEFAULT_DEVICE_TYPE])
    profile: dict[str, Any] = spec["template"]["energy_profile"]
    return deepcopy(profile)


def normalize_circuit_templates(config_data: Any) -> None:
    """Fill in omitted ``energy_profile`` blocks, in place, before validation.

    Typed ``Any`` for the same reason ``validate_yaml_config`` next door is:
    callers hold either a raw ``dict`` from ``yaml.safe_load`` or a
    ``SimulationConfig`` TypedDict, and a TypedDict is not assignable to
    ``dict[str, Any]`` — mypy rejects it, since mutating one through a plain-dict
    alias could violate its declared keys. Narrowing the parameter would push a
    cast onto every caller and buy nothing real.


    Two steps, both keyed off what the template already declares:

    1. A template with no ``energy_profile`` gets the default for its
       ``device_type`` -- producer shape for ``pv``, high-power consumer for
       ``evse``, ordinary consumer otherwise.
    2. Energy-profile keys written at template top level are moved into the
       profile. An explicit value always wins over the default, whichever level
       it was written at, so this cannot silently override what a config says.
    """
    templates = config_data.get("circuit_templates")
    if not isinstance(templates, dict):
        return

    for template in templates.values():
        if not isinstance(template, dict):
            continue
        device_type = template.get("device_type", _DEFAULT_DEVICE_TYPE)
        if "energy_profile" not in template:
            template["energy_profile"] = default_energy_profile(device_type)
        profile = template["energy_profile"]
        if not isinstance(profile, dict):
            continue
        for key in _PROMOTABLE_TO_PROFILE:
            if key in template:
                profile[key] = template[key]
