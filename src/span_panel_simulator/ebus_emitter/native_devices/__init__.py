"""Emitter-native devices.

A native device is a configured-and-self-driving entity that lives inside the emitter
and computes its per-tick state from its configuration plus per-tick context pushed by
the producer (simulator). Reflector-class devices (circuits, PV, EVSE, lugs) are
producer-driven and do not have a corresponding NativeDevice.

Today's native devices: BESS, LoadShedding. MID + PCS are future when the parent-child
schema lands. The list is intentionally small — circuits stay 100% producer-driven
even when the producer's view of them is HVAC-shaped or recorder-replayed.
"""

from span_panel_simulator.ebus_emitter.native_devices.bess import (
    BESSConfig,
    BESSDevice,
    ChargeMode,
)
from span_panel_simulator.ebus_emitter.native_devices.load_shedding import (
    LoadSheddingConfig,
    LoadSheddingDevice,
)
from span_panel_simulator.ebus_emitter.native_devices.protocol import (
    NativeDevice,
    NativeTickContext,
)

__all__ = [
    "BESSConfig",
    "BESSDevice",
    "ChargeMode",
    "LoadSheddingConfig",
    "LoadSheddingDevice",
    "NativeDevice",
    "NativeTickContext",
]
