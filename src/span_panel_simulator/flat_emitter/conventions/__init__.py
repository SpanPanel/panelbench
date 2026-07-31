"""Centralized panel-wiring conventions used across the emitter.

Each module in this package isolates a single physical-layer convention so the
rest of the codebase can be convention-agnostic. Convention changes (e.g.,
supporting European single-phase or 3-phase commercial panels) land here without
rippling into ``PanelMeter``, ``EnergyIntegrator``, or per-property derivations.
"""

from span_panel_simulator.flat_emitter.conventions.tab_legs import Leg, legs_for_tabs

__all__ = ["Leg", "legs_for_tabs"]
