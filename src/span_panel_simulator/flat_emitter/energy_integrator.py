"""Per-instance energy accumulator with producer-supplied seed values.

Each tick the producer reports an instantaneous power for an instance. The
integrator advances ``produced_wh`` and ``consumed_wh`` based on the elapsed
time since that instance's last tick, computed from ``current_time`` (epoch
seconds) the producer pushes. Power-sign convention matches the rest of the
emitter: positive = consumption (load), negative = production (PV/V2G).

Seeding:
- ``register(instance_id)`` initializes the integrator at zero.
- ``seed(instance_id, *, consumed_wh, produced_wh)`` overwrites the running
  accumulators (typical use: producer reads last-known values from persistent
  storage and seeds at startup before the first tick).
- The manifest's ``initial-consumed-wh`` / ``initial-produced-wh`` keys are a
  declarative alternative; ``Emitter`` calls ``seed()`` from those at startup.

Time bookkeeping:
- The first observation for an instance establishes ``last_tick_time_s`` but
  does NOT integrate (no prior interval). Subsequent observations integrate
  ``power * (now - last)``.
- Backwards or zero ``dt`` is treated as a no-op (clock skew / duplicate tick)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EnergyState:
    consumed_wh: float = 0.0
    produced_wh: float = 0.0
    last_tick_time_s: float | None = None


class EnergyIntegrator:
    """Accumulators for many instances. One instance per circuit / PV / EVSE."""

    def __init__(self) -> None:
        self._states: dict[str, EnergyState] = {}

    def register(self, instance_id: str) -> None:
        """Idempotent — repeated calls leave existing state untouched."""
        if instance_id not in self._states:
            self._states[instance_id] = EnergyState()

    def seed(
        self,
        instance_id: str,
        *,
        consumed_wh: float = 0.0,
        produced_wh: float = 0.0,
    ) -> None:
        """Overwrite accumulators for ``instance_id``. Raises ``KeyError`` if the
        instance was never registered (catches typos before the first tick)."""
        if instance_id not in self._states:
            raise KeyError(
                f"seed() called for unknown instance_id={instance_id!r}; call register() first"
            )
        st = self._states[instance_id]
        st.consumed_wh = consumed_wh
        st.produced_wh = produced_wh
        # Note: last_tick_time_s is intentionally NOT reset — seeding only
        # overwrites the energy values, the time bookkeeping persists.

    def observe(self, instance_id: str, power_w: float, current_time: float) -> None:
        """Advance the integrator for ``instance_id`` with the producer's signed
        ``power_w`` reported at ``current_time``. The first call for an instance
        establishes ``last_tick_time_s`` without integrating."""
        if instance_id not in self._states:
            raise KeyError(f"observe() for unknown instance_id={instance_id!r}")
        st = self._states[instance_id]
        if st.last_tick_time_s is None:
            st.last_tick_time_s = current_time
            return
        dt_s = current_time - st.last_tick_time_s
        st.last_tick_time_s = current_time
        if dt_s <= 0:
            return
        dt_h = dt_s / 3600.0
        if power_w > 0:
            st.consumed_wh += power_w * dt_h
        elif power_w < 0:
            st.produced_wh += -power_w * dt_h

    def state(self, instance_id: str) -> EnergyState:
        return self._states[instance_id]

    def known(self, instance_id: str) -> bool:
        return instance_id in self._states
