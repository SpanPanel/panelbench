"""EnergySystem — top-level energy balance resolver for grid + PV + load.

BESS dispatch lives in the emitter (`span_panel_simulator.flat_emitter.native_devices.bess`); this
module is battery-blind. The simulator computes pre-battery grid power; the
emitter publishes battery state separately and consumers (HA, dashboards)
correlate the two streams.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_simulator.energy.bus import PanelBus
from span_panel_simulator.energy.components import GridMeter, LoadGroup, PVSource
from span_panel_simulator.energy.types import (
    EnergySystemConfig,
    PowerInputs,
    SystemState,
)

if TYPE_CHECKING:
    from span_panel_simulator.energy.components import Component


class EnergySystem:
    """Component-based energy balance resolver (grid + PV + load).

    Instantiate via ``from_config()``.  Call ``tick()`` each simulation step to
    resolve power flows across the bus.
    """

    def __init__(
        self,
        bus: PanelBus,
        grid: GridMeter,
        pv: PVSource | None,
        load: LoadGroup,
    ) -> None:
        self.bus = bus
        self.grid = grid
        self.pv = pv
        self.load = load
        self.islandable: bool = False

    @property
    def grid_state(self) -> str:
        """``ON_GRID`` or ``OFF_GRID`` based on grid connection status."""
        return "OFF_GRID" if not self.grid.connected else "ON_GRID"

    @staticmethod
    def from_config(config: EnergySystemConfig) -> EnergySystem:
        grid = GridMeter(connected=config.grid.connected)

        pv: PVSource | None = None
        if config.pv is not None:
            pv = PVSource(available_power_w=0.0, online=True)

        total_demand = sum(lc.demand_w for lc in config.loads)
        load = LoadGroup(demand_w=total_demand)

        components: list[Component] = [load]
        if pv is not None:
            components.append(pv)
        components.append(grid)

        bus = PanelBus(components=components)
        return EnergySystem(bus=bus, grid=grid, pv=pv, load=load)

    def tick(self, ts: float, inputs: PowerInputs) -> SystemState:
        del ts  # Not used post-BESS-removal; kept for caller signature stability.

        self.grid.connected = inputs.grid_connected
        pv_allowed = inputs.grid_connected or self.islandable

        if self.pv is not None:
            self.pv.online = pv_allowed
            self.pv.available_power_w = inputs.pv_available_w

        self.load.demand_w = inputs.load_demand_w

        bus_state = self.bus.resolve()

        # PV curtailment for islanded operation: when grid is disconnected the
        # SLACK component cannot absorb surplus production. Clamp PV to demand
        # and re-resolve so all component effective values stay consistent.
        if (
            self.pv is not None
            and self.pv.online
            and not inputs.grid_connected
            and not bus_state.is_balanced()
        ):
            surplus_w = bus_state.total_supply_w - bus_state.total_demand_w
            if surplus_w > 0:
                curtailed = max(0.0, self.pv.available_power_w - surplus_w)
                self.pv.available_power_w = curtailed
                bus_state = self.bus.resolve()

        pv_power = 0.0
        if self.pv is not None and self.pv.online:
            pv_power = self.pv.available_power_w

        return SystemState(
            grid_power_w=bus_state.grid_power_w,
            pv_power_w=pv_power,
            load_power_w=inputs.load_demand_w,
            balanced=bus_state.is_balanced(),
        )
