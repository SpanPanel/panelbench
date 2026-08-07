"""Core types for the component-based energy system. Battery (BESS) modeling lives
in the emitter (`panelbench.ebus_emitter.native_devices.bess`) — this
module only covers the grid + PV + load balance the simulator owns."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class ComponentRole(IntEnum):
    """Role of a component on the bus, ordered by resolution priority."""

    LOAD = 1
    SOURCE = 2
    SLACK = 3


@dataclass
class PowerContribution:
    """Power contribution from a single component on the bus.

    All values are non-negative magnitudes; direction is expressed by
    which field is populated (demand_w vs supply_w).
    """

    demand_w: float = 0.0
    supply_w: float = 0.0


@dataclass
class BusState:
    """Aggregate state of the energy bus at a point in time."""

    total_demand_w: float = 0.0
    total_supply_w: float = 0.0
    grid_power_w: float = 0.0

    @property
    def net_deficit_w(self) -> float:
        """Positive means demand exceeds supply; negative means surplus."""
        return self.total_demand_w - self.total_supply_w

    def is_balanced(self) -> bool:
        """Return True when grid power accounts for any residual imbalance."""
        return abs(self.total_demand_w - self.total_supply_w - self.grid_power_w) < 0.01


@dataclass
class PowerInputs:
    """External inputs fed into the energy resolution pipeline.

    Contains only raw measurements and grid status. Battery dispatch is
    resolved by the emitter's BESS native device, not here.
    """

    pv_available_w: float = 0.0
    load_demand_w: float = 0.0
    grid_connected: bool = True


@dataclass
class SystemState:
    """Resolved system state after energy dispatch (grid + PV + load only)."""

    grid_power_w: float
    pv_power_w: float
    load_power_w: float
    balanced: bool


@dataclass(frozen=True)
class GridConfig:
    """Configuration for the utility grid connection."""

    connected: bool = True


@dataclass(frozen=True)
class PVConfig:
    """Configuration for a solar PV inverter."""

    nameplate_w: float = 0.0
    inverter_type: str = "ac_coupled"


@dataclass(frozen=True)
class LoadConfig:
    """Configuration for a load group."""

    demand_w: float = 0.0


@dataclass(frozen=True)
class EnergySystemConfig:
    """Top-level configuration for the energy system (grid + PV + loads only)."""

    grid: GridConfig
    pv: PVConfig | None = None
    loads: list[LoadConfig] = field(default_factory=list)
