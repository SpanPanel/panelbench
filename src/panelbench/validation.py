"""Standalone configuration validation functions.

Extracted from DynamicSimulationEngine so that both the engine and the
dashboard can validate configs without circular imports.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from panelbench.const import SHED_PRIORITIES
from panelbench.emitter_adapter.spec_generator import relay_locked
from panelbench.firmware import SPAN_RELEASE_202639, panel_firmware_version, predates
from panelbench.hardware import (
    panel_hardware_version,
    panel_variant,
    publishes_outside_meters,
    relay_locks_priority,
)
from panelbench.pv_rating import rating_conflicts
from panelbench.pv_section import bound_pv_circuit_id
from panelbench.registration_limit import registration_limit

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ebus_panel_sim import Variant

_LOGGER = logging.getLogger(__name__)


def validate_yaml_config(config_data: Any) -> None:
    """Validate YAML configuration structure and required fields."""
    if not isinstance(config_data, dict):
        raise ValueError("YAML configuration must be a dictionary")

    required_sections = ["panel_config", "circuit_templates", "circuits"]
    for section in required_sections:
        if section not in config_data:
            raise ValueError(f"Missing required section: {section}")

    validate_panel_config(config_data["panel_config"])
    validate_circuit_templates(config_data["circuit_templates"])
    _validate_commissioned_firmware(
        panel_firmware_version(config_data), config_data["circuit_templates"]
    )
    _validate_commissioned_variant(config_data, config_data["circuit_templates"])
    validate_circuits(config_data["circuits"], config_data["circuit_templates"])
    _warn_uncommissioned_pv_circuits(
        panel_firmware_version(config_data),
        panel_variant(config_data),
        config_data["circuits"],
        config_data["circuit_templates"],
    )
    validate_pv_section(config_data)
    validate_ratings(config_data)
    validate_outside_meters(config_data)
    validate_shared_circuits(config_data["circuits"])

    if "panel_source" in config_data:
        validate_panel_source(config_data["panel_source"])


def validate_panel_config(panel_config: Any) -> None:
    """Validate panel configuration section."""
    if not isinstance(panel_config, dict):
        raise ValueError("panel_config must be a dictionary")

    required_panel_fields = ["serial_number", "total_tabs", "main_size"]
    for field in required_panel_fields:
        if field not in panel_config:
            raise ValueError(f"Missing required panel_config field: {field}")
    registration_limit(panel_config)


def validate_circuit_templates(circuit_templates: Any) -> None:
    """Validate circuit templates section."""
    if not isinstance(circuit_templates, dict):
        raise ValueError("circuit_templates must be a dictionary")

    if not circuit_templates:
        raise ValueError("At least one circuit template must be defined")

    for template_name, template in circuit_templates.items():
        validate_single_template(template_name, template)


def validate_single_template(template_name: str, template: Any) -> None:
    """Validate a single circuit template."""
    if not isinstance(template, dict):
        raise ValueError(f"Circuit template '{template_name}' must be a dictionary")

    required_template_fields = [
        "energy_profile",
        "relay_behavior",
        "priority",
    ]
    for field in required_template_fields:
        if field not in template:
            raise ValueError(
                f"Missing required field '{field}' in circuit template '{template_name}'"
            )
    priority = str(template["priority"]).upper()
    if priority not in SHED_PRIORITIES:
        raise ValueError(
            f"Circuit template '{template_name}' has priority {template['priority']!r}; "
            f"it must be one of {', '.join(SHED_PRIORITIES)}"
        )
    _validate_commissioned_system(template_name, template)


_COMMISSIONED_SYSTEMS = ("pv", "backup")


def _validate_commissioned_system(template_name: str, template: Mapping[str, object]) -> None:
    """Every fact the emitter enforces for a commissioned-system circuit, stated in the config.

    The emitter rejects the circuit at construction otherwise, which surfaces as a
    panel that will not start. Refusing it here names the template instead.
    """
    system = template.get("commissioned_system")
    if system is None:
        return
    prefix = f"Circuit template '{template_name}' is a commissioned-system circuit"
    if system not in _COMMISSIONED_SYSTEMS:
        raise ValueError(f"{prefix}: commissioned_system must be 'pv' or 'backup', got {system!r}")
    if str(template["priority"]).upper() != "NEVER":
        raise ValueError(f"{prefix}, which is permanently NEVER: set priority: NEVER")
    if not relay_locked(str(template["relay_behavior"])):
        raise ValueError(
            f"{prefix}, which has a locked relay: set relay_behavior: non-controllable"
        )
    if template.get("never_backup"):
        raise ValueError(
            f"{prefix}, which is a different lock from never_backup (permanently OFF_GRID): "
            "remove never_backup"
        )


def _validate_commissioned_firmware(
    firmware: str, circuit_templates: Mapping[str, Mapping[str, object]]
) -> None:
    """Commissioned-system circuits are locked only from SPAN release 202639.

    Before it, SPAN published the "Commissioned PV System" and "Commissioned Backup
    System" circuits switchable and re-prioritisable (SPAN-API-Client-Docs, Release
    202639). The emitter does not key the lock on the firmware, so a config naming an
    earlier release must not ask for one: refused here, naming the template, rather
    than published as a lock that release never had.

    *circuit_templates* has already passed `validate_circuit_templates`, so every
    template is a mapping.
    """
    if not predates(firmware, SPAN_RELEASE_202639):
        return
    for template_name, template in circuit_templates.items():
        if template.get("commissioned_system") is not None:
            raise ValueError(
                f"Circuit template '{template_name}' is a commissioned-system circuit, which is "
                f"locked only from SPAN release 202639, but firmware_version is {firmware!r}: "
                "remove commissioned_system, or name release 202639 or later"
            )


def _validate_commissioned_variant(
    config_data: Mapping[str, object], circuit_templates: Mapping[str, Mapping[str, object]]
) -> None:
    """A variant whose locked relay locks the priority has no commissioned-system circuits.

    A PV or battery breaker there is an ordinary circuit with a locked relay at
    priority NEVER, and the emitter refuses the key, so it is refused here naming the
    template. *circuit_templates* has already passed `validate_circuit_templates`.
    """
    variant = panel_variant(config_data)
    if not relay_locks_priority(variant):
        return
    for template_name, template in circuit_templates.items():
        if template.get("commissioned_system") is not None:
            raise ValueError(
                f"Circuit template '{template_name}' is a commissioned-system circuit, which "
                f"the {variant!r} variant does not have: remove commissioned_system, and a "
                "non-controllable relay at priority NEVER locks it as that variant does"
            )


def _warn_uncommissioned_pv_circuits(
    firmware: str,
    variant: Variant,
    circuits: list[Mapping[str, object]],
    circuit_templates: Mapping[str, Mapping[str, object]],
) -> None:
    """From SPAN release 202639 a circuit feeding an inverter is a locked one.

    That release publishes every commissioned inverter as its own PV device, named by
    the circuit feeding it, and locks the circuits the panel adds for a commissioned
    PV system (SPAN-API-Client-Docs, Release 202639). A circuit names a device it
    feeds only when that device is commissioned, so on that release a PV circuit
    without ``commissioned_system: pv`` publishes a switchable, re-prioritisable
    circuit the release never does.

    Not under a variant whose locked relay locks the priority, which has no
    commissioned-system circuits.

    Warned, not refused: a config that loaded before must keep loading, such as a
    template shipped before the key existed, and a panel clone recognises a
    commissioned circuit only by the name SPAN gives it, so it can produce one. A PV
    circuit with no tabs, a what-if one, is fed by no breaker.

    *circuits* and *circuit_templates* have already passed validation, so every
    circuit names a template that exists.
    """
    if predates(firmware, SPAN_RELEASE_202639) or relay_locks_priority(variant):
        return
    for circuit in circuits:
        template_name = str(circuit["template"])
        template = circuit_templates[template_name]
        if (
            template.get("device_type") == "pv"
            and template.get("commissioned_system") != "pv"
            and circuit.get("tabs")
        ):
            _LOGGER.warning(
                "Circuit %r feeds a solar inverter, which SPAN release 202639 and later "
                "lock as a commissioned PV system, but firmware_version is %r and its "
                "template %r has no commissioned_system: pv, so its relay and priority "
                "stay settable; give the template commissioned_system: pv, priority: NEVER "
                "and relay_behavior: non-controllable",
                circuit["id"],
                firmware,
                template_name,
            )


def validate_circuits(circuits: Any, circuit_templates: dict[str, Any]) -> None:
    """Validate circuits section."""
    if not isinstance(circuits, list):
        raise ValueError("circuits must be a list")

    if not circuits:
        raise ValueError("At least one circuit must be defined")

    for i, circuit in enumerate(circuits):
        validate_single_circuit(i, circuit, circuit_templates)


def validate_single_circuit(index: int, circuit: Any, circuit_templates: dict[str, Any]) -> None:
    """Validate a single circuit definition."""
    if not isinstance(circuit, dict):
        raise ValueError(f"Circuit {index} must be a dictionary")

    required_circuit_fields = ["id", "name", "template"]
    for field in required_circuit_fields:
        if field not in circuit:
            raise ValueError(f"Missing required field '{field}' in circuit {index}")

    template_name = circuit["template"]
    if template_name not in circuit_templates:
        raise ValueError(f"Circuit {index} references unknown template '{template_name}'")

    template = circuit_templates.get(template_name, {})

    tabs = circuit.get("tabs", [])
    if not isinstance(tabs, list):
        raise ValueError(f"Circuit {index} ('tabs') must be a list")

    # Infrastructure entities (PV, EVSE) may have empty tabs
    # when added as virtual devices for "what-if" modeling.
    is_infrastructure = template.get("device_type") in ("pv", "evse")
    if not tabs and not is_infrastructure:
        raise ValueError(f"Circuit {index} ('tabs') must be a non-empty list")

    if len(tabs) == 2:
        validate_double_pole_tabs(index, circuit.get("name", f"circuit {index}"), tabs)
    if len(tabs) == 1 and (
        template.get("device_type") == "pv" or template.get("commissioned_system") == "pv"
    ):
        # Warned, not refused: a 120 V PV circuit is unusual rather than impossible,
        # and a config that loaded before must keep loading.
        _LOGGER.warning(
            "Circuit %r feeds a solar inverter from one tab (%s); a grid-tied inverter in "
            "a US panel is 240 V on a two-pole breaker, two tabs on opposite legs",
            circuit["id"],
            tabs[0],
        )


def validate_pv_section(config_data: Mapping[str, object]) -> None:
    """Validate which inverter the ``pv`` section describes.

    Refused here, naming the circuits, rather than when the panel builds its
    manifest; ``pv_section.bound_pv_circuit_id`` states the rule. *config_data*'s
    templates and circuits have already passed validation.
    """
    bound_pv_circuit_id(config_data)


def validate_ratings(config_data: Mapping[str, object]) -> None:
    """Refuse a circuit rated in two places with different values.

    ``pv_rating`` states the rule. The loader folds every legacy rating it can into
    the profile, so what is left here is a disagreement, and picking either value
    would be a guess.
    """
    conflicts = rating_conflicts(config_data)
    if conflicts:
        raise ValueError("; ".join(conflicts))


def validate_outside_meters(config_data: Mapping[str, object]) -> None:
    """Meters outside the panel: each a mapping with an id no circuit or meter shares,
    on a panel whose variant publishes them.

    A meter's device id is scoped from its ``id`` as a circuit's is, so a shared id
    would publish two devices on one topic. *config_data*'s circuits have already
    passed validation.
    """
    meters = config_data.get("outside_meters")
    if meters is None:
        return
    if not isinstance(meters, list):
        raise ValueError("outside_meters must be a list")
    circuits = config_data.get("circuits")
    taken = (
        {str(c["id"]) for c in circuits if isinstance(c, dict)}
        if isinstance(circuits, list)
        else set()
    )
    for index, meter in enumerate(meters):
        if not isinstance(meter, dict) or "id" not in meter:
            raise ValueError(f"outside_meters[{index}] must be a mapping with an id")
        meter_id = str(meter["id"])
        if meter_id in taken:
            raise ValueError(
                f"outside_meters[{index}] has id {meter_id!r}, which is already taken"
            )
        taken.add(meter_id)
    if meters and not publishes_outside_meters(config_data):
        raise ValueError(
            f"outside_meters are not published by the {panel_variant(config_data)!r} variant, "
            f"which hardware_version {panel_hardware_version(config_data)!r} selects"
        )


def validate_shared_circuits(circuits: list[Mapping[str, object]]) -> None:
    """Each circuit a circuit says it shares a meter and relay with is another circuit
    of the panel. *circuits* has already passed validation."""
    ids = {str(circuit["id"]) for circuit in circuits}
    for circuit in circuits:
        peers = circuit.get("shared_with") or []
        if not isinstance(peers, list):
            raise ValueError(f"Circuit {circuit['id']!r} shared_with must be a list")
        for peer in peers:
            if str(peer) not in ids or str(peer) == str(circuit["id"]):
                raise ValueError(
                    f"Circuit {circuit['id']!r} shares its meter with {peer!r}, "
                    "which is no other circuit of this panel"
                )


def validate_panel_source(panel_source: Any) -> None:
    """Validate panel_source block for clone provenance."""
    if not isinstance(panel_source, dict):
        raise ValueError("panel_source must be a dictionary")

    required_fields = ["origin_serial", "host"]
    for field in required_fields:
        if field not in panel_source:
            raise ValueError(f"Missing required panel_source field: {field}")

    if not isinstance(panel_source["origin_serial"], str):
        raise ValueError("panel_source.origin_serial must be a string")

    if not isinstance(panel_source["host"], str):
        raise ValueError("panel_source.host must be a string")


def validate_double_pole_tabs(index: int, name: str, tabs: list[int]) -> None:
    """Validate that a double-pole (240V) circuit uses a valid tab pair.

    A valid pair must be:
    - Same parity (both odd or both even)
    - Exactly 2 apart
    """
    a, b = sorted(tabs)

    if a % 2 != b % 2:
        raise ValueError(
            f"Circuit {index} ('{name}') has double-pole tabs {tabs} with mixed parity. "
            f"Both tabs must be odd or both even."
        )

    if b - a != 2:
        raise ValueError(
            f"Circuit {index} ('{name}') has double-pole tabs {tabs} that are "
            f"{b - a} apart. Double-pole tabs must be exactly 2 apart."
        )
