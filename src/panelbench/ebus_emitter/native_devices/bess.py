"""Native BESS device — configured-and-self-driving.

Configuration is supplied at construction (via ``Emitter(bess_configs=...)``) and
mutable mid-run via ``Emitter.update_bess_config``. Per-tick context — grid
state, instantaneous load demand, instantaneous PV available, current_time —
is pushed by the producer via ``Emitter.publish_tick``; the emitter calls
``BESSDevice.tick`` and writes the resulting ``EbusBatterySnapshot`` into the
internal snapshot.

Subclassable for vendor-variant behaviour (Powerwall vs Enphase IQ etc.)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from panelbench.ebus_emitter.native_devices.protocol import NativeTickContext
from panelbench.ebus_emitter.snapshot import EbusBatterySnapshot

ChargeMode = Literal["self-consumption", "backup-only"]
DispatchState = Literal["charging", "discharging", "idle"]


@dataclass(slots=True)
class BESSConfig:
    """BESS device configuration. Producer supplies at construction; mutation through
    ``BESSDevice.update_config`` takes effect on the next tick."""

    instance_id: str
    nameplate_capacity_kwh: float
    max_charge_w: float
    max_discharge_w: float
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    backup_reserve_pct: float = 20.0
    charge_mode: ChargeMode = "self-consumption"
    charge_hours: tuple[int, ...] = ()
    discharge_hours: tuple[int, ...] = ()
    initial_soc_pct: float = 50.0


@dataclass(slots=True)
class BESSDevice:
    """Per-tick BESS state machine. State (SOC/SOE) accumulates across ticks; config
    can be replaced live without restart."""

    config: BESSConfig
    _soc_pct: float = field(init=False)
    _soe_kwh: float = field(init=False)
    _last_tick_time: float | None = field(init=False, default=None)
    _state: DispatchState = field(init=False, default="idle")

    def __post_init__(self) -> None:
        self._soc_pct = self.config.initial_soc_pct
        self._soe_kwh = self.config.nameplate_capacity_kwh * (self.config.initial_soc_pct / 100.0)

    @property
    def instance_id(self) -> str:
        """Stable identifier for this device — delegates to ``config.instance_id``.

        Exposed as a property so ``BESSDevice`` structurally satisfies the
        ``NativeDevice[EbusBatterySnapshot]`` Protocol."""
        return self.config.instance_id

    def update_config(self, config: BESSConfig) -> None:
        """Replace configuration; SOC/SOE persist."""
        self.config = config

    def set_soe(self, soe_kwh: float) -> None:
        """Overwrite stored SOE/SOC. Used by ``Emitter.seed_bess_soe`` to restore
        battery state across emitter restarts."""
        capped = max(0.0, min(self.config.nameplate_capacity_kwh, soe_kwh))
        self._soe_kwh = capped
        if self.config.nameplate_capacity_kwh > 0:
            self._soc_pct = (capped / self.config.nameplate_capacity_kwh) * 100.0
        else:
            self._soc_pct = 0.0

    def tick(self, ctx: NativeTickContext) -> EbusBatterySnapshot:
        """Run one dispatch step and return a fresh ``EbusBatterySnapshot``.

        Given the current grid state, instantaneous load demand, and PV
        availability for this tick, decide whether to charge, discharge, or
        idle; integrate energy in/out of the cell since the previous tick;
        update internal SOC/SOE; and return a snapshot describing the result.
        Pure with respect to the panel snapshot — does not read or mutate it."""
        dispatch_w = self._decide_dispatch(
            current_time=ctx.current_time,
            grid_online=ctx.grid_online,
            load_demand_w=ctx.load_demand_w,
            pv_available_w=ctx.pv_available_w,
        )

        if self._last_tick_time is not None:
            dt_seconds = max(0.0, ctx.current_time - self._last_tick_time)
            dt_hours = dt_seconds / 3600.0
            if dispatch_w > 0:
                self._soe_kwh -= (
                    dispatch_w * dt_hours / 1000.0
                ) / self.config.discharge_efficiency
            elif dispatch_w < 0:
                self._soe_kwh += (
                    abs(dispatch_w) * dt_hours / 1000.0
                ) * self.config.charge_efficiency
            self._soe_kwh = max(0.0, min(self.config.nameplate_capacity_kwh, self._soe_kwh))
            self._soc_pct = (self._soe_kwh / self.config.nameplate_capacity_kwh) * 100.0
        self._last_tick_time = ctx.current_time

        if dispatch_w > 0:
            self._state = "discharging"
        elif dispatch_w < 0:
            self._state = "charging"
        else:
            self._state = "idle"

        return EbusBatterySnapshot(
            soe_percentage=self._soc_pct,
            soe_kwh=self._soe_kwh,
            active_power_w=dispatch_w,
            nameplate_capacity_kwh=self.config.nameplate_capacity_kwh,
            communication="OK",
        )

    @property
    def state(self) -> DispatchState:
        return self._state

    def _decide_dispatch(
        self,
        *,
        current_time: float,
        grid_online: bool,
        load_demand_w: float,
        pv_available_w: float,
    ) -> float:
        """Return signed dispatch power in watts. Positive = discharge, negative = charge."""
        # Reserve floor: do not discharge below backup reserve when grid is online.
        backup_floor_kwh = (
            self.config.nameplate_capacity_kwh * self.config.backup_reserve_pct / 100.0
        )

        if not grid_online:
            # Off-grid: discharge to meet load demand minus PV (down to empty).
            deficit_w = max(0.0, load_demand_w - pv_available_w)
            if self._soe_kwh <= 0:
                return 0.0
            return min(deficit_w, self.config.max_discharge_w)

        # Grid online — apply mode-specific behavior.
        pv_surplus_w = max(0.0, pv_available_w - load_demand_w)
        if self.config.charge_mode == "backup-only":
            # Keep reserve while on-grid. Charge only from PV surplus; never
            # charge from utility grid and never discharge in backup-only mode.
            if pv_surplus_w > 0 and self._soe_kwh < self.config.nameplate_capacity_kwh:
                return -min(
                    self.config.max_charge_w,
                    pv_surplus_w,
                    (self.config.nameplate_capacity_kwh - self._soe_kwh) * 1000.0,
                )
            return 0.0

        # self-consumption mode — always reactive: charge from PV surplus,
        # discharge to cover load deficit. Hour-of-day windows are NOT applied
        # (those belong to a TOU/custom dispatch mode, not modelled here).
        # ``backup_floor_kwh`` still gates discharge so the reserve isn't
        # consumed during normal operation.
        del current_time  # not used in self-consumption
        load_deficit_w = max(0.0, load_demand_w - pv_available_w)

        if pv_surplus_w > 0 and self._soe_kwh < self.config.nameplate_capacity_kwh:
            return -min(
                self.config.max_charge_w,
                pv_surplus_w,
                (self.config.nameplate_capacity_kwh - self._soe_kwh) * 1000.0,
            )

        if load_deficit_w > 0 and self._soe_kwh > backup_floor_kwh:
            available_kwh = self._soe_kwh - backup_floor_kwh
            return min(
                load_deficit_w,
                self.config.max_discharge_w,
                available_kwh * 1000.0,
            )

        return 0.0
