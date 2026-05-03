"""Build a ``DeviceManifest`` from a loaded clone-profile dict.

The manifest carries identity + physics keys per the v0.3.0 emitter contract.
The emitter parses physics fields via ``ManifestPhysicsView`` at construction
and uses them for relay-state ownership, energy integration, panel-meter
aggregation, and per-leg current calculation.

``build_manifest`` is a thin orchestrator: it asks each ``_xxx_instance(s)``
helper to build its slice of the manifest and concatenates the results.
Adding a new device class (e.g. MID, second BESS) is a single ``append``
on the orchestrator and a new helper — no need to touch the rest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebus_emitter import DeviceInstance, DeviceManifest

from span_panel_simulator.emitter_adapter.instance_ids import stable_circuit_uuid
from span_panel_simulator.panel_models import PANEL_SIZE_TO_MODEL

if TYPE_CHECKING:
    from span_panel_simulator.config_types import CircuitTemplateExtended, SimulationConfig

_CIRCUIT_RELAY_BEHAVIOR_MAP = {
    "controllable": "controllable",
    "non_controllable": "non-controllable",
    "non-controllable": "non-controllable",
    "always_on": "always-on",
    "always-on": "always-on",
}

_PV_INVERTER_TYPE_MAP = {
    "hybrid": "hybrid",
    "ac_coupled": "ac-coupled",
    "ac-coupled": "ac-coupled",
}


def build_manifest(profile: SimulationConfig) -> DeviceManifest:
    """Walk the loaded SimulationConfig dict; emit a DeviceManifest the emitter
    consumes. Identity + physics — no behaviour, no schedule, no modelling."""
    instances: list[DeviceInstance] = [
        _panel_instance(profile),
        *_lugs_instances(profile),
        *_circuit_instances(profile),
    ]
    bess = _bess_instance(profile)
    if bess is not None:
        instances.append(bess)
    pv = _pv_instance(profile)
    if pv is not None:
        instances.append(pv)
    evse = _evse_instance(profile)
    if evse is not None:
        instances.append(evse)
    return DeviceManifest(instances=tuple(instances))


def _panel_instance(profile: SimulationConfig) -> DeviceInstance:
    panel_cfg = profile["panel_config"]
    panel_id = panel_cfg["serial_number"]
    panel_size = int(panel_cfg.get("total_tabs", 40))
    panel_model = PANEL_SIZE_TO_MODEL.get(panel_size, f"MAIN_{panel_size}")
    return DeviceInstance(
        entity_class="panel",
        instance_id=panel_id,
        display_name=panel_cfg.get("display_name", "Span Panel"),
        metadata={
            "vendor-name": "Span",
            "serial-number": panel_id,
            "firmware-version": profile.get("firmware_version", "sim/v0.1.0"),
            "hardware-version": profile.get("hardware_version", "rev2"),
            "panel-size": str(panel_size),
            "main-breaker-rating-a": str(int(panel_cfg.get("main_size", 200))),
            "panel-model": panel_model,
            "postal-code": str(panel_cfg.get("postal_code", "94103")),
            "time-zone": str(panel_cfg.get("time_zone", "America/Los_Angeles")),
            "service-voltage-v": str(panel_cfg.get("service_voltage_v", 240.0)),
            "line-voltage-v": str(panel_cfg.get("line_voltage_v", 120.0)),
            "islandable": "true" if _islandable(profile) else "false",
        },
    )


def _lugs_instances(profile: SimulationConfig) -> list[DeviceInstance]:
    panel_id = profile["panel_config"]["serial_number"]
    return [
        DeviceInstance(
            entity_class="lugs",
            instance_id=f"{panel_id}-lugs-upstream",
            display_name="Upstream lugs",
            metadata={"direction": "upstream"},
        ),
        DeviceInstance(
            entity_class="lugs",
            instance_id=f"{panel_id}-lugs-downstream",
            display_name="Downstream lugs",
            metadata={"direction": "downstream"},
        ),
    ]


def _circuit_instances(profile: SimulationConfig) -> list[DeviceInstance]:
    templates = profile.get("circuit_templates") or {}
    instances: list[DeviceInstance] = []
    for c in profile.get("circuits") or []:
        tabs = c.get("tabs") or [0]
        template_name = c.get("template", "")
        template: CircuitTemplateExtended | None = templates.get(template_name)
        if template is None:
            relay_behavior_raw = "controllable"
            priority = "NICE_TO_HAVE"
            breaker_rating = 20.0
        else:
            relay_behavior_raw = str(template.get("relay_behavior", "controllable"))
            priority = str(template.get("priority", "NICE_TO_HAVE")).upper()
            # ``breaker_rating_a`` is the producer-side legacy key; the typed
            # ``breaker_rating`` (no units suffix) is the canonical YAML field.
            # Read both so manifests built from older clones still work.
            breaker_rating = float(
                template.get("breaker_rating_a") or template.get("breaker_rating", 20),
            )
        relay_behavior = _CIRCUIT_RELAY_BEHAVIOR_MAP.get(
            relay_behavior_raw.lower().replace("_", "-"),
            "controllable",
        )
        instances.append(
            DeviceInstance(
                entity_class="circuit",
                instance_id=stable_circuit_uuid(c["id"]),
                display_name=c.get("name", c["id"]),
                metadata={
                    "tab-numbers": ",".join(str(int(t)) for t in tabs if t),
                    "breaker-rating-a": str(breaker_rating),
                    "default-priority": priority,
                    "relay-behavior": relay_behavior,
                    "placement": str(c.get("placement", "downstream-of-lugs")),
                    "always-on": "true" if relay_behavior == "always-on" else "false",
                },
            ),
        )
    return instances


def _bess_instance(profile: SimulationConfig) -> DeviceInstance | None:
    bess_cfg = profile.get("bess") or {}
    if not bess_cfg.get("enabled"):
        return None
    panel_id = profile["panel_config"]["serial_number"]
    bess_meta: dict[str, str] = {
        "vendor-name": str(bess_cfg.get("vendor", "Span")),
        "nameplate-capacity-kwh": str(bess_cfg.get("nameplate_capacity_kwh", 13.5)),
    }
    if "initial_soe_kwh" in bess_cfg:
        bess_meta["initial-soe-kwh"] = str(bess_cfg["initial_soe_kwh"])
    return DeviceInstance(
        entity_class="bess",
        instance_id=f"{panel_id}-bess",
        display_name="Battery",
        metadata=bess_meta,
    )


def _pv_instance(profile: SimulationConfig) -> DeviceInstance | None:
    pv_cfg = profile.get("pv") or {}
    if not pv_cfg.get("enabled"):
        return None
    panel_id = profile["panel_config"]["serial_number"]
    inverter_type_raw = str(pv_cfg.get("inverter_type", "ac_coupled"))
    inverter_type = _PV_INVERTER_TYPE_MAP.get(
        inverter_type_raw.lower().replace("_", "-"),
        "ac-coupled",
    )
    return DeviceInstance(
        entity_class="pv",
        instance_id=f"{panel_id}-pv",
        display_name="Solar",
        metadata={
            "vendor-name": str(pv_cfg.get("vendor", "Enphase")),
            "nameplate-capacity-w": str(pv_cfg.get("nameplate_capacity_w", 5000.0)),
            "inverter-type": inverter_type,
        },
    )


def _evse_instance(profile: SimulationConfig) -> DeviceInstance | None:
    evse_cfg = profile.get("evse") or {}
    if not evse_cfg.get("enabled"):
        return None
    panel_id = profile["panel_config"]["serial_number"]
    # Per the v0.3.0 emitter migration guide, ``software-version`` was
    # renamed to ``firmware-version`` for all device classes.
    return DeviceInstance(
        entity_class="evse",
        instance_id=f"{panel_id}-evse",
        display_name="EV Charger",
        metadata={
            "vendor-name": str(evse_cfg.get("vendor", "SPAN")),
            "product-name": str(evse_cfg.get("product", "SPAN Drive")),
            "part-number": str(evse_cfg.get("part_number", "SPN-DRV-001")),
            "serial-number": str(evse_cfg.get("serial_number", f"SIM-EVSE-{panel_id}")),
            "firmware-version": str(evse_cfg.get("firmware_version", "sim/v0.1.0")),
            "max-current-a": str(evse_cfg.get("max_current_a", 32.0)),
        },
    )


def _islandable(profile: SimulationConfig) -> bool:
    """A panel can island when a hybrid PV inverter is configured (or any config
    explicitly sets ``islandable: true`` on panel_config)."""
    panel_cfg = profile["panel_config"]
    if "islandable" in panel_cfg:
        return bool(panel_cfg["islandable"])
    pv_cfg = profile.get("pv") or {}
    return bool(pv_cfg.get("enabled") and pv_cfg.get("inverter_type") == "hybrid")
