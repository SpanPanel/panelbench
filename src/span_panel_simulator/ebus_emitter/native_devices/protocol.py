"""Native-device tick contract — shared by BESS and future natives.

Future natives include MID and vendor-specific BESS variants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable


@dataclass(frozen=True, slots=True)
class NativeTickContext:
    """Per-tick driving signal handed to a native device's ``tick()``.

    All natives consume the same primitive signals; per-device behaviour lives
    in the device implementation, not in the context shape."""

    current_time: float
    grid_online: bool
    load_demand_w: float
    pv_available_w: float


SnapT = TypeVar("SnapT", covariant=True)


@runtime_checkable
class NativeDevice(Protocol[SnapT]):
    """Common contract for emitter-resident, configured-and-self-driving devices.

    Today: ``BESSDevice``. Future: ``MidDevice`` (when MID becomes a separate
    device per the upcoming eBus migration), vendor-specific BESS variants
    (Powerwall vs Enphase IQ specifics)."""

    @property
    def instance_id(self) -> str: ...

    def tick(self, ctx: NativeTickContext) -> SnapT: ...
