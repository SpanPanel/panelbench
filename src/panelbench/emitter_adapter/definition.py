"""What a PanelBench panel *is*, as the emitter takes it: one ``PanelDefinition``.

The config stays the source of truth; it also says how the panel behaves, which a
definition deliberately does not. This module reads the makeup half of it — the
manifest, the native battery and the load-shedding policy — and, for import,
writes the emitter-option half back, so both directions live side by side and
cannot drift apart unnoticed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebus_panel_sim import BESSConfig, ChargeMode, LoadSheddingConfig, PanelDefinition

from panelbench.emitter_adapter.instance_ids import bess_device_id
from panelbench.emitter_adapter.spec_generator import build_manifest

if TYPE_CHECKING:
    from panelbench.config_types import BESSConfigYAML, PanelConfig, SimulationConfig

_DEFAULT_SOC_SHED_THRESHOLD_PCT = 20.0
_DEFAULT_INITIAL_SOC_PCT = 50.0
"""``BESSConfig``'s own default, used when the YAML states no starting charge."""


def build_definition(config: SimulationConfig) -> PanelDefinition:
    """The panel *config* describes, as one definition the emitter is built from."""
    battery = bess_config(config["panel_config"]["serial_number"], config.get("bess") or {})
    return PanelDefinition(
        manifest=build_manifest(config),
        bess_configs=() if battery is None else (battery,),
        load_shedding=load_shedding_config(config["panel_config"]),
    )


def bess_config(serial_number: str, bess: BESSConfigYAML) -> BESSConfig | None:
    """The emitter's native battery for a YAML ``bess`` section, or ``None`` when disabled."""
    if not bess.get("enabled"):
        return None
    raw_mode = bess.get("charge_mode", "self-consumption")
    mode: ChargeMode = "backup-only" if raw_mode == "backup-only" else "self-consumption"
    return BESSConfig(
        instance_id=bess_device_id(serial_number, bess),
        nameplate_capacity_kwh=float(bess.get("nameplate_capacity_kwh", 13.5)),
        max_charge_w=float(bess.get("max_charge_w", 3500.0)),
        max_discharge_w=float(bess.get("max_discharge_w", 3500.0)),
        charge_efficiency=float(bess.get("charge_efficiency", 0.95)),
        discharge_efficiency=float(bess.get("discharge_efficiency", 0.95)),
        backup_reserve_pct=float(bess.get("backup_reserve_pct", 20.0)),
        charge_mode=mode,
        charge_hours=tuple(bess.get("charge_hours", [10, 11, 12, 13, 14, 15])),
        discharge_hours=tuple(bess.get("discharge_hours", [17, 18, 19, 20, 21])),
        initial_soc_pct=_initial_soc_pct(bess),
    )


def _initial_soc_pct(bess: BESSConfigYAML) -> float:
    """The battery's starting charge as the emitter's percentage.

    The YAML states it once, as ``initial_soe_kwh``, which also seeds the manifest's
    ``initial-soe-kwh``; a second key for the same fact could disagree with it.
    Absent, or with no capacity to divide by, it is the emitter's own default.
    """
    soe = bess.get("initial_soe_kwh")
    nameplate = float(bess.get("nameplate_capacity_kwh", 13.5))
    if soe is None or nameplate <= 0:
        return _DEFAULT_INITIAL_SOC_PCT
    return float(soe) / nameplate * 100.0


def load_shedding_config(panel: PanelConfig) -> LoadSheddingConfig:
    """The panel's off-grid shed policy."""
    return LoadSheddingConfig(
        soc_threshold_pct=float(panel.get("soc_shed_threshold", _DEFAULT_SOC_SHED_THRESHOLD_PCT)),
    )
