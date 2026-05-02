"""Generate the (DeviceManifest, RuntimeSpec) pair the emitter consumes from a
SimulationConfig.

The simulator's clone-profile YAML is the source of truth. This generator walks the
loaded SimulationConfig dict, derives the static identity (DeviceManifest) and the
behaviour (RuntimeSpec) artifacts, and returns both. Producer-side modelling
(weather/HVAC/solar physics) bakes its outputs into the runtime spec; the emitter
consumes pre-baked data only.

This v0.1.0 generator handles the structural translation. The deeper modelling baking
(annual solar curves from sun position + cloud cover; HVAC seasonal multipliers folded
into per-circuit monthly_factors; rate-driven BESS schedule resolution) is deferred to
a follow-up release; for now the spec carries flat default factor tables that exercise
the emitter's full pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ebus_emitter import DeviceInstance, DeviceManifest, RuntimeSpec
from ebus_emitter.scheduleRunner.runtime_spec import (
    BESSConfig,
    CircuitSpec,
    ClockSpec,
    CyclingPatternSpec,
    EnergyProfileSpec,
    GridSpec,
    PanelSpec,
    TimeOfDaySpec,
)

from span_panel_simulator.emitter_adapter.instance_ids import stable_circuit_uuid

_PRIORITY_V1_TO_V2 = {
    "MUST_HAVE": "NEVER",
    "NICE_TO_HAVE": "SOC_THRESHOLD",
    "NON_ESSENTIAL": "OFF_GRID",
    "NEVER": "NEVER",
    "SOC_THRESHOLD": "SOC_THRESHOLD",
    "OFF_GRID": "OFF_GRID",
    "UNKNOWN": "UNKNOWN",
}

_CHARGE_MODE_MAP = {
    "self-consumption": "self-consumption",
    "backup-only": "backup-only",
    "solar-gen": "self-consumption",
    "custom": "self-consumption",
}


def _normalise_priority(p: str) -> str:
    return _PRIORITY_V1_TO_V2.get(p, "UNKNOWN")


@dataclass(frozen=True, slots=True)
class GeneratedArtifacts:
    manifest: DeviceManifest
    runtime_spec: RuntimeSpec


def build_manifest(profile: dict[str, Any]) -> DeviceManifest:
    panel_cfg = profile["panel_config"]
    panel_id = panel_cfg["serial_number"]
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

    for c in profile.get("circuits", []):
        instances.append(
            DeviceInstance(
                entity_class="circuit",
                instance_id=stable_circuit_uuid(c["id"]),
                display_name=c.get("name", c["id"]),
                metadata={
                    "tab-number": str(c.get("tabs", [0])[0] if c.get("tabs") else 0),
                    "dipole": str(len(c.get("tabs", [])) > 1).lower(),
                },
            )
        )

    bess_cfg = profile.get("bess") or {}
    if bess_cfg.get("enabled"):
        instances.append(
            DeviceInstance(
                entity_class="bess",
                instance_id=f"{panel_id}-bess",
                display_name="Battery",
                metadata={
                    "vendor-name": str(bess_cfg.get("vendor", "Span")),
                    "nameplate-capacity": str(bess_cfg.get("nameplate_capacity_kwh", 13.5)),
                },
            )
        )

    pv_cfg = profile.get("pv") or {}
    if pv_cfg.get("enabled"):
        instances.append(
            DeviceInstance(
                entity_class="pv",
                instance_id=f"{panel_id}-pv",
                display_name="Solar",
                metadata={"vendor-name": str(pv_cfg.get("vendor", "Enphase"))},
            )
        )

    evse_cfg = profile.get("evse") or {}
    if evse_cfg.get("enabled"):
        instances.append(
            DeviceInstance(
                entity_class="evse",
                instance_id=f"{panel_id}-evse",
                display_name="EV Charger",
                metadata={"vendor-name": str(evse_cfg.get("vendor", "Span"))},
            )
        )

    return DeviceManifest(instances=tuple(instances))


def build_runtime_spec(profile: dict[str, Any]) -> RuntimeSpec:
    panel_cfg = profile["panel_config"]
    panel_id = panel_cfg["serial_number"]
    sim_params = profile.get("simulation_params", {}) or {}

    bess_cfg = profile.get("bess") or {}
    bess_config: BESSConfig | None = None
    if bess_cfg.get("enabled"):
        raw_mode = bess_cfg.get("charge_mode", "self-consumption")
        mode = _CHARGE_MODE_MAP.get(raw_mode, "self-consumption")
        bess_config = BESSConfig(
            instance_id=f"{panel_id}-bess",
            nameplate_capacity_kwh=float(bess_cfg.get("nameplate_capacity_kwh", 13.5)),
            max_charge_w=float(bess_cfg.get("max_charge_w", 3500.0)),
            max_discharge_w=float(bess_cfg.get("max_discharge_w", 3500.0)),
            charge_efficiency=float(bess_cfg.get("charge_efficiency", 0.95)),
            discharge_efficiency=float(bess_cfg.get("discharge_efficiency", 0.95)),
            backup_reserve_pct=float(bess_cfg.get("backup_reserve_pct", 20.0)),
            charge_mode=mode,
            charge_hours=tuple(bess_cfg.get("charge_hours", [10, 11, 12, 13, 14, 15])),
            discharge_hours=tuple(
                bess_cfg.get("discharge_hours", [17, 18, 19, 20, 21]),
            ),
        )

    default_hours = {
        0: 0.30,
        1: 0.25,
        2: 0.20,
        3: 0.20,
        4: 0.20,
        5: 0.25,
        6: 0.45,
        7: 0.65,
        8: 0.55,
        9: 0.40,
        10: 0.35,
        11: 0.40,
        12: 0.50,
        13: 0.45,
        14: 0.40,
        15: 0.45,
        16: 0.55,
        17: 0.75,
        18: 0.85,
        19: 0.90,
        20: 0.85,
        21: 0.70,
        22: 0.55,
        23: 0.40,
    }
    default_months = {m: 1.0 for m in range(1, 13)}

    circuits: list[CircuitSpec] = []
    templates = profile.get("circuit_templates", {})
    for c in profile.get("circuits", []):
        template_name = c.get("template", "")
        template = templates.get(template_name, {})
        ep = template.get("energy_profile", {})
        cycling = template.get("cycling_pattern") or {}
        time_of_day = template.get("time_of_day_profile") or {}

        hour_factors = (
            {int(k): float(v) for k, v in time_of_day.get("hour_factors", {}).items()}
            if time_of_day.get("enabled")
            else dict(default_hours)
        )
        if set(hour_factors.keys()) != set(range(24)):
            hour_factors = dict(default_hours)

        monthly_factors = {
            int(k): float(v) for k, v in template.get("monthly_factors", {}).items()
        } or dict(default_months)
        if set(monthly_factors.keys()) != set(range(1, 13)):
            monthly_factors = dict(default_months)

        circuits.append(
            CircuitSpec(
                instance_id=stable_circuit_uuid(c["id"]),
                energy_profile=EnergyProfileSpec(
                    mode="producer" if ep.get("mode") == "producer" else "consumer",
                    typical_power_w=float(ep.get("typical_power", 200.0)),
                    power_variation=float(ep.get("power_variation", 0.15)),
                    initial_consumed_energy_wh=float(
                        ep.get("initial_consumed_energy_wh", 0.0),
                    ),
                    initial_produced_energy_wh=float(
                        ep.get("initial_produced_energy_wh", 0.0),
                    ),
                    nameplate_capacity_w=ep.get("nameplate_capacity_w"),
                ),
                time_of_day=TimeOfDaySpec(hour_factors=hour_factors),
                monthly_factors=monthly_factors,
                priority=_normalise_priority(template.get("priority", "UNKNOWN")),
                relay_behavior=(
                    "controllable"
                    if template.get("relay_behavior") == "controllable"
                    else "non_controllable"
                ),
                cycling_pattern=CyclingPatternSpec(
                    enabled=bool(cycling),
                    duty_cycle=float(cycling.get("duty_cycle", 0.0)),
                    period_seconds=int(cycling.get("period", 0)),
                ),
            )
        )

    return RuntimeSpec(
        panel=PanelSpec(instance_id=panel_id, setter_debounce_minutes=15),
        clock=ClockSpec(
            start_iso=str(
                sim_params.get("simulation_start_time") or "2026-01-01T00:00:00+00:00",
            ),
            acceleration=float(sim_params.get("time_acceleration", 1.0)),
        ),
        grid=GridSpec(mode="always-available"),
        bess=bess_config,
        solar_curve=None,
        circuits=tuple(circuits),
    )


def generate(profile: dict[str, Any]) -> GeneratedArtifacts:
    return GeneratedArtifacts(
        manifest=build_manifest(profile),
        runtime_spec=build_runtime_spec(profile),
    )
