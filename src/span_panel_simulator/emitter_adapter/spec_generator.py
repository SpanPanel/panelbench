"""Build a ``DeviceManifest`` from a loaded clone-profile dict.

The manifest carries identity + physics keys per the v0.3.0 emitter contract.
The emitter parses physics fields via ``ManifestPhysicsView`` at construction
and uses them for relay-state ownership, energy integration, panel-meter
aggregation, and per-leg current calculation."""

from __future__ import annotations

from typing import Any

from ebus_emitter import DeviceInstance, DeviceManifest

from span_panel_simulator.emitter_adapter.instance_ids import stable_circuit_uuid
from span_panel_simulator.panel_models import PANEL_SIZE_TO_MODEL

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


def build_manifest(profile: dict[str, Any]) -> DeviceManifest:
    """Walk the loaded SimulationConfig dict; emit a DeviceManifest the emitter
    consumes. Identity + physics — no behaviour, no schedule, no modelling."""
    panel_cfg = profile["panel_config"]
    panel_id = panel_cfg["serial_number"]
    panel_size = int(panel_cfg.get("total_tabs", 40))
    panel_model = PANEL_SIZE_TO_MODEL.get(panel_size, f"MAIN_{panel_size}")

    instances: list[DeviceInstance] = [
        DeviceInstance(
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
        ),
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

    templates = profile.get("circuit_templates", {})
    for c in profile.get("circuits", []):
        tabs = c.get("tabs") or [0]
        template = templates.get(c.get("template", ""), {})
        relay_behavior_raw = str(template.get("relay_behavior", "controllable"))
        relay_behavior = _CIRCUIT_RELAY_BEHAVIOR_MAP.get(
            relay_behavior_raw.lower().replace("_", "-"),
            "controllable",
        )
        priority = str(template.get("priority", "NICE_TO_HAVE")).upper()
        breaker_rating = float(template.get("breaker_rating_a", 20.0))
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

    bess_cfg = profile.get("bess") or {}
    if bess_cfg.get("enabled"):
        bess_meta: dict[str, str] = {
            "vendor-name": str(bess_cfg.get("vendor", "Span")),
            "nameplate-capacity-kwh": str(bess_cfg.get("nameplate_capacity_kwh", 13.5)),
        }
        if "initial_soe_kwh" in bess_cfg:
            bess_meta["initial-soe-kwh"] = str(bess_cfg["initial_soe_kwh"])
        instances.append(
            DeviceInstance(
                entity_class="bess",
                instance_id=f"{panel_id}-bess",
                display_name="Battery",
                metadata=bess_meta,
            ),
        )

    pv_cfg = profile.get("pv") or {}
    if pv_cfg.get("enabled"):
        inverter_type_raw = str(pv_cfg.get("inverter_type", "ac_coupled"))
        inverter_type = _PV_INVERTER_TYPE_MAP.get(
            inverter_type_raw.lower().replace("_", "-"),
            "ac-coupled",
        )
        instances.append(
            DeviceInstance(
                entity_class="pv",
                instance_id=f"{panel_id}-pv",
                display_name="Solar",
                metadata={
                    "vendor-name": str(pv_cfg.get("vendor", "Enphase")),
                    "nameplate-capacity-w": str(pv_cfg.get("nameplate_capacity_w", 5000.0)),
                    "inverter-type": inverter_type,
                },
            ),
        )

    evse_cfg = profile.get("evse") or {}
    if evse_cfg.get("enabled"):
        instances.append(
            DeviceInstance(
                entity_class="evse",
                instance_id=f"{panel_id}-evse",
                display_name="EV Charger",
                metadata={
                    "vendor-name": str(evse_cfg.get("vendor", "SPAN")),
                    "product-name": str(evse_cfg.get("product", "SPAN Drive")),
                    "part-number": str(evse_cfg.get("part_number", "SPN-DRV-001")),
                    "serial-number": str(evse_cfg.get("serial_number", f"SIM-EVSE-{panel_id}")),
                    "software-version": str(evse_cfg.get("software_version", "sim/v0.1.0")),
                    "max-current-a": str(evse_cfg.get("max_current_a", 32.0)),
                },
            ),
        )

    return DeviceManifest(instances=tuple(instances))


def _islandable(profile: dict[str, Any]) -> bool:
    """A panel can island when a hybrid PV inverter is configured (or any config
    explicitly sets ``islandable: true`` on panel_config)."""
    panel_cfg = profile.get("panel_config", {})
    if isinstance(panel_cfg, dict) and "islandable" in panel_cfg:
        return bool(panel_cfg["islandable"])
    pv_cfg = profile.get("pv") or {}
    return bool(pv_cfg.get("enabled") and pv_cfg.get("inverter_type") == "hybrid")
