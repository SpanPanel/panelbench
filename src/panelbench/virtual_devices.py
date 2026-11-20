"""A circuit on no breaker space is a virtual device: modelled, never published.

The dashboard adds a PV inverter or a SPAN Drive as a circuit with no spaces for a what-if
(validation allows one for a ``pv`` or ``evse`` template alone). The Model view models it,
but a panel publishes only the circuits on its spaces, and the emitter refuses a circuit
with none, so it is left out of all the emitter is handed: the manifest's circuits, the
devices they feed, and each tick's circuit powers and SPAN Drive readings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def on_the_panel(circuit: Mapping[str, object]) -> bool:
    """Whether *circuit* sits on a breaker space, and so is published."""
    return bool(circuit.get("tabs"))
