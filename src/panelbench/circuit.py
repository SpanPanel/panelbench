"""SimulatedCircuit — per-circuit simulation state.

Each instance is constructed once (at engine init / reload) with its
circuit definition, resolved template, and a shared RealisticBehaviorEngine
reference.  The engine calls ``tick()`` each cycle, then reads properties
to drive the emitter (which builds the transport-agnostic snapshots).
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Final, cast

from panelbench.virtual_devices import on_the_panel

# The circuit overrides that land in its template's energy profile; any other lands in
# the template itself.
OVERRIDDEN_PROFILE_KEYS: Final = frozenset(
    {"mode", "power_range", "typical_power", "power_variation", "efficiency"}
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from panelbench.config_types import (
        CircuitDefinitionExtended,
        CircuitTemplateExtended,
    )
    from panelbench.engine import RealisticBehaviorEngine


def effective_template(
    template: CircuitTemplateExtended, circuit: CircuitDefinitionExtended
) -> CircuitTemplateExtended:
    """*circuit*'s template as the panel models and publishes it
    (``effective_settings``), for code reading a validated config."""
    # The template's keys, set from the circuit's settings under the same keys, which
    # validation checks on this result as it checks a template's own.
    return cast("CircuitTemplateExtended", effective_settings(template, circuit))


def effective_settings(
    template: Mapping[str, object], circuit: Mapping[str, object]
) -> dict[str, object]:
    """*circuit*'s template as the panel models and publishes it.

    A copy of *template* with the circuit's own ``recorder_entity``, its ``overrides``
    (``OVERRIDDEN_PROFILE_KEYS`` into the energy profile, any other into the template),
    and its own ``breaker_rating``, which replaces the template's under either key, as
    the dashboard's rating edit writes it. The engine, the definition and validation
    all read a circuit through this, so what is checked is what is modelled, and what
    is modelled is what is published.
    """
    merged: dict[str, object] = deepcopy(dict(template))
    recorder_entity = circuit.get("recorder_entity")
    if recorder_entity is not None:
        merged["recorder_entity"] = recorder_entity
    profile = merged.get("energy_profile")
    overrides = circuit.get("overrides")
    for key, value in overrides.items() if isinstance(overrides, dict) else ():
        if key in OVERRIDDEN_PROFILE_KEYS and isinstance(profile, dict):
            profile[key] = value
        else:
            merged[key] = value
    if "breaker_rating" in circuit:
        merged["breaker_rating"] = circuit["breaker_rating"]
        merged.pop("breaker_rating_a", None)
    return merged


class SimulatedCircuit:
    """Encapsulates the state and logic for a single simulated circuit."""

    def __init__(
        self,
        circuit_def: CircuitDefinitionExtended,
        template: CircuitTemplateExtended,
        behavior_engine: RealisticBehaviorEngine,
    ) -> None:
        self._circuit_def = circuit_def
        # The circuit's recorder link, overrides and own rating, over its template.
        self._template = effective_template(template, circuit_def)
        self._behavior_engine = behavior_engine

        # Derived from template (stable across ticks)
        self._energy_mode: str = self._template["energy_profile"]["mode"]
        self._device_type_str: str = self._derive_device_type()

        # Mutable per-tick state
        self._instant_power_w = 0.0
        self._relay_state = "CLOSED"
        self._priority = self._template["priority"]
        self._produced_energy_wh = 0.0
        self._consumed_energy_wh = 0.0
        self._last_energy_update: float | None = None
        self._last_tick_time = 0

        # Dynamic overrides (set by dashboard / API)
        self._overrides: dict[str, object] = {}

        # Seed energy counters: prefer explicit seeds from clone, fall back to estimate
        ep = self._template["energy_profile"]
        initial_consumed = ep.get("initial_consumed_energy_wh")
        initial_produced = ep.get("initial_produced_energy_wh")
        if initial_consumed is not None or initial_produced is not None:
            self._consumed_energy_wh = float(initial_consumed) if initial_consumed else 0.0
            self._produced_energy_wh = float(initial_produced) if initial_produced else 0.0
        else:
            produced, consumed = behavior_engine.estimate_annual_energy_wh(self._template)
            self._produced_energy_wh, self._consumed_energy_wh = produced, consumed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(self, current_time: float, *, power_override: float | None = None) -> None:
        """Advance the circuit by one simulation step.

        Args:
            current_time: Simulation timestamp (seconds since epoch).
            power_override: If set, use this power instead of behaviour engine
                            (used for tab-sync groups where the engine computes
                            the split externally).
        """
        # Apply state overrides (relay, priority) before power computation
        # so get_circuit_power sees the current relay state immediately.
        self._apply_state_overrides()

        # Compute base power
        if power_override is not None:
            base_power = power_override
        else:
            base_power = self._behavior_engine.get_circuit_power(
                self._template,
                current_time,
                self._relay_state,
            )

        self._instant_power_w = base_power

        # Apply power overrides after computation
        self._apply_power_overrides()

        # Accumulate energy
        self._accumulate_energy(current_time)

        self._last_tick_time = int(current_time)

    def apply_override(self, overrides: dict[str, object]) -> None:
        """Set dynamic overrides (from dashboard / REST API)."""
        self._overrides.update(overrides)

    def clear_overrides(self) -> None:
        """Remove all dynamic overrides."""
        self._overrides.clear()

    # ------------------------------------------------------------------
    # Properties (for engine aggregation)
    # ------------------------------------------------------------------

    @property
    def circuit_id(self) -> str:
        return self._circuit_def["id"]

    @property
    def name(self) -> str:
        return str(self._circuit_def["name"])

    @property
    def instant_power_w(self) -> float:
        return self._instant_power_w

    @property
    def device_type(self) -> str:
        return self._device_type_str

    @property
    def energy_mode(self) -> str:
        return self._energy_mode

    @property
    def produced_energy_wh(self) -> float:
        return self._produced_energy_wh

    @property
    def consumed_energy_wh(self) -> float:
        return self._consumed_energy_wh

    @property
    def tabs(self) -> list[int]:
        return self._circuit_def.get("tabs", [])

    @property
    def on_the_panel(self) -> bool:
        """Whether this circuit sits on a breaker space, and so is published; a virtual
        device is modelled alone (``virtual_devices``)."""
        return on_the_panel(self._circuit_def)

    @property
    def template_name(self) -> str:
        return str(self._circuit_def["template"])

    @property
    def template(self) -> CircuitTemplateExtended:
        return self._template

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _derive_device_type(self) -> str:
        """Derive device_type from the template.

        Checks for an explicit ``device_type`` field first, then falls back
        to mode-based detection.
        """
        explicit = self._template.get("device_type")
        if explicit:
            return explicit
        mode = self._template.get("energy_profile", {}).get("mode", "consumer")
        if mode == "producer":
            return "pv"
        if mode == "bidirectional":
            return "evse"
        return "circuit"

    def _apply_state_overrides(self) -> None:
        """Apply relay and priority overrides before power computation."""
        if not self._overrides:
            return
        if "relay_state" in self._overrides:
            self._relay_state = str(self._overrides["relay_state"])
        if "priority" in self._overrides:
            self._priority = str(self._overrides["priority"])

    def _apply_power_overrides(self) -> None:
        """Apply power overrides after power computation."""
        if not self._overrides:
            return
        if "power_override" in self._overrides:
            self._instant_power_w = float(self._overrides["power_override"])  # type: ignore[arg-type]
        elif "power_multiplier" in self._overrides:
            self._instant_power_w *= float(self._overrides["power_multiplier"])  # type: ignore[arg-type]
        if self._relay_state == "OPEN":
            self._instant_power_w = 0.0

    def _accumulate_energy(self, current_time: float) -> None:
        """Unified energy accumulation — replaces three separate methods."""
        if self._last_energy_update is None:
            self._last_energy_update = current_time
            return

        time_elapsed_hours = (current_time - self._last_energy_update) / 3600.0
        self._last_energy_update = current_time

        if self._instant_power_w <= 0:
            return

        energy_increment = self._instant_power_w * time_elapsed_hours

        if self._energy_mode == "producer":
            self._produced_energy_wh += energy_increment
        else:
            # Consumer and bidirectional (EVSE/V2G): without per-circuit
            # direction telemetry the energy is conservatively counted as
            # consumption.  BESS is GFE on the upstream lugs and not a
            # circuit, so the only bidirectional case at this level is V2G.
            self._consumed_energy_wh += energy_increment
