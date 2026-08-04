import pytest

from span_panel_simulator.flat_emitter.energy_integrator import EnergyIntegrator


def test_register_initializes_at_zero() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    st = ei.state("c1")
    assert st.consumed_wh == 0.0
    assert st.produced_wh == 0.0
    assert st.last_tick_time_s is None


def test_register_is_idempotent() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.observe("c1", 1000.0, 0.0)
    ei.observe("c1", 1000.0, 3600.0)
    assert ei.state("c1").consumed_wh == 1000.0
    ei.register("c1")  # should not reset
    assert ei.state("c1").consumed_wh == 1000.0


def test_first_observe_does_not_integrate() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.observe("c1", 1000.0, 0.0)
    st = ei.state("c1")
    assert st.consumed_wh == 0.0
    assert st.last_tick_time_s == 0.0


def test_consumption_integration_one_hour_at_1000w() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.observe("c1", 1000.0, 0.0)
    ei.observe("c1", 1000.0, 3600.0)
    assert ei.state("c1").consumed_wh == pytest.approx(1000.0)
    assert ei.state("c1").produced_wh == 0.0


def test_production_integration_negative_power() -> None:
    ei = EnergyIntegrator()
    ei.register("pv1")
    ei.observe("pv1", -2000.0, 0.0)
    ei.observe("pv1", -2000.0, 1800.0)  # half hour
    assert ei.state("pv1").produced_wh == pytest.approx(1000.0)
    assert ei.state("pv1").consumed_wh == 0.0


def test_zero_power_does_not_change_accumulators() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.observe("c1", 0.0, 0.0)
    ei.observe("c1", 0.0, 3600.0)
    assert ei.state("c1").consumed_wh == 0.0
    assert ei.state("c1").produced_wh == 0.0


def test_backwards_dt_is_no_op() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.observe("c1", 1000.0, 100.0)
    ei.observe("c1", 1000.0, 50.0)  # clock went backwards
    assert ei.state("c1").consumed_wh == 0.0
    # last_tick_time_s should still update so subsequent ticks integrate from
    # the new (earlier) baseline.
    assert ei.state("c1").last_tick_time_s == 50.0


def test_seed_overwrites_accumulators() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.seed("c1", consumed_wh=10000.0, produced_wh=500.0)
    assert ei.state("c1").consumed_wh == 10000.0
    assert ei.state("c1").produced_wh == 500.0


def test_seed_preserves_time_bookkeeping() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.observe("c1", 1000.0, 100.0)  # establishes last_tick_time_s = 100
    ei.seed("c1", consumed_wh=99999.0)
    assert ei.state("c1").last_tick_time_s == 100.0


def test_seed_unknown_id_raises() -> None:
    ei = EnergyIntegrator()
    with pytest.raises(KeyError, match="unknown instance_id"):
        ei.seed("ghost", consumed_wh=1.0)


def test_observe_unknown_id_raises() -> None:
    ei = EnergyIntegrator()
    with pytest.raises(KeyError, match="unknown instance_id"):
        ei.observe("ghost", 100.0, 0.0)


def test_independent_instances_dont_cross_contaminate() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.register("c2")
    ei.observe("c1", 1000.0, 0.0)
    ei.observe("c2", 500.0, 0.0)
    ei.observe("c1", 1000.0, 3600.0)
    ei.observe("c2", 500.0, 3600.0)
    assert ei.state("c1").consumed_wh == pytest.approx(1000.0)
    assert ei.state("c2").consumed_wh == pytest.approx(500.0)


def test_known_returns_true_after_register() -> None:
    ei = EnergyIntegrator()
    assert ei.known("c1") is False
    ei.register("c1")
    assert ei.known("c1") is True


def test_seed_after_initial_observe_then_continued_ticks_carry_seed_baseline() -> None:
    ei = EnergyIntegrator()
    ei.register("c1")
    ei.seed("c1", consumed_wh=5000.0)
    ei.observe("c1", 1000.0, 0.0)
    ei.observe("c1", 1000.0, 3600.0)
    assert ei.state("c1").consumed_wh == pytest.approx(6000.0)
