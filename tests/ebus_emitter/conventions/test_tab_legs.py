import pytest

from span_panel_simulator.ebus_emitter.conventions.tab_legs import Leg, legs_for_tabs


def test_odd_tab_lands_on_l1() -> None:
    assert legs_for_tabs((1,)) == (Leg.L1,)
    assert legs_for_tabs((3,)) == (Leg.L1,)
    assert legs_for_tabs((39,)) == (Leg.L1,)


def test_even_tab_lands_on_l2() -> None:
    assert legs_for_tabs((2,)) == (Leg.L2,)
    assert legs_for_tabs((4,)) == (Leg.L2,)
    assert legs_for_tabs((40,)) == (Leg.L2,)


def test_standard_dipole_spans_both_legs() -> None:
    assert legs_for_tabs((1, 2)) == (Leg.L1, Leg.L2)
    assert legs_for_tabs((39, 40)) == (Leg.L1, Leg.L2)


def test_non_adjacent_dipole_does_not_span_both_legs() -> None:
    # Convention is mechanical; ManifestPhysicsView is responsible for catching
    # mis-declared dipoles. legs_for_tabs just reports the truth.
    assert legs_for_tabs((1, 3)) == (Leg.L1, Leg.L1)
    assert legs_for_tabs((2, 4)) == (Leg.L2, Leg.L2)


def test_empty_tabs_returns_empty() -> None:
    assert legs_for_tabs(()) == ()


def test_zero_or_negative_tab_raises() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        legs_for_tabs((0,))
    with pytest.raises(ValueError, match="must be >= 1"):
        legs_for_tabs((-1,))
    with pytest.raises(ValueError, match="must be >= 1"):
        legs_for_tabs((1, 0))
