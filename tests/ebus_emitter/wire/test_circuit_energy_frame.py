"""Circuit power/energy reference-frame tests.

The snapshot is device-frame (``instant_power_w`` positive = the circuit is
consuming; ``consumed_energy_wh`` accumulates that consumption). The Homie wire
is **enclosure-frame**: values describe flow relative to the enclosure busbar.

    imported-energy  = energy imported BY THE ENCLOSURE from the circuit (backfeed)
    exported-energy  = energy exported BY THE ENCLOSURE to the circuit (load)
    active-power > 0 = flowing into the enclosure (backfeed)
    active-power < 0 = flowing out of the enclosure to a load

These tests exist because the energy accumulators were previously published
un-relabelled while ``active-power`` was correctly negated, so a pure load
published a *rising* ``imported-energy`` and a flat ``exported-energy`` —
internally inconsistent with its own power sign, and inverted relative to real
SPAN panel firmware. Consumers reading the wire saw every load circuit as
producing energy.
"""

from __future__ import annotations

from span_panel_simulator.flat_emitter.snapshot import (
    EbusCircuitSnapshot,
    EbusPanelInfo,
    EbusPanelSnapshot,
)
from span_panel_simulator.flat_emitter.wire.bag_builder import _RESOLVERS

_IMPORTED = _RESOLVERS[("circuit", "circuit/imported-energy")]
_EXPORTED = _RESOLVERS[("circuit", "circuit/exported-energy")]
_ACTIVE_POWER = _RESOLVERS[("circuit", "circuit/active-power")]


def _snapshot(circuit: EbusCircuitSnapshot) -> EbusPanelSnapshot:
    """A panel snapshot carrying a single circuit — the only field these
    resolvers read."""
    return EbusPanelSnapshot(
        info=EbusPanelInfo(serial_number="test-panel", firmware_version="test/v0"),
        circuits={circuit.circuit_id: circuit},
    )


def _wh(value: object) -> float:
    """Narrow a resolver's ``object`` return to a float for comparison."""
    assert isinstance(value, float)
    return value


def _circuit(
    *,
    instant_power_w: float,
    consumed_energy_wh: float,
    produced_energy_wh: float,
) -> EbusCircuitSnapshot:
    return EbusCircuitSnapshot(
        circuit_id="c1",
        name="Test Circuit",
        relay_state="CLOSED",
        instant_power_w=instant_power_w,
        produced_energy_wh=produced_energy_wh,
        consumed_energy_wh=consumed_energy_wh,
        tabs=[1],
        priority="NEVER",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )


def test_load_circuit_exports_energy_and_reads_negative_power() -> None:
    """A pure load: the enclosure is *exporting* energy to it."""
    snap = _snapshot(
        _circuit(instant_power_w=300.0, consumed_energy_wh=4430.0, produced_energy_wh=0.0)
    )

    assert _ACTIVE_POWER(snap, "c1") == -300.0
    assert _EXPORTED(snap, "c1") == 4430.0
    assert _IMPORTED(snap, "c1") == 0.0


def test_backfeeding_circuit_imports_energy_and_reads_positive_power() -> None:
    """A PV inverter on a breaker: the enclosure is *importing* energy from it."""
    snap = _snapshot(
        _circuit(instant_power_w=-8500.0, consumed_energy_wh=0.0, produced_energy_wh=141.6)
    )

    assert _ACTIVE_POWER(snap, "c1") == 8500.0
    assert _IMPORTED(snap, "c1") == 141.6
    assert _EXPORTED(snap, "c1") == 0.0


def test_power_sign_agrees_with_the_accumulator_that_grows() -> None:
    """The regression guard: integrating published power must agree with the
    published accumulator. Negative power (load) must pair with exported-energy;
    positive power (backfeed) must pair with imported-energy."""
    load = _snapshot(
        _circuit(instant_power_w=300.0, consumed_energy_wh=4430.0, produced_energy_wh=0.0)
    )
    backfeed = _snapshot(
        _circuit(instant_power_w=-8500.0, consumed_energy_wh=0.0, produced_energy_wh=141.6)
    )

    assert _wh(_ACTIVE_POWER(load, "c1")) < 0
    assert _wh(_EXPORTED(load, "c1")) > 0, "negative power must grow exported-energy"
    assert _wh(_IMPORTED(load, "c1")) == 0

    assert _wh(_ACTIVE_POWER(backfeed, "c1")) > 0
    assert _wh(_IMPORTED(backfeed, "c1")) > 0, "positive power must grow imported-energy"
    assert _wh(_EXPORTED(backfeed, "c1")) == 0


def test_resolvers_return_none_for_unknown_circuit() -> None:
    snap = _snapshot(
        _circuit(instant_power_w=300.0, consumed_energy_wh=1.0, produced_energy_wh=0.0)
    )

    assert _IMPORTED(snap, "missing") is None
    assert _EXPORTED(snap, "missing") is None
    assert _ACTIVE_POWER(snap, "missing") is None
