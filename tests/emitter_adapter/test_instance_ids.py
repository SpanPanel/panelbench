from span_panel_simulator.emitter_adapter.instance_ids import stable_circuit_uuid


def test_stable_circuit_uuid_is_dashless() -> None:
    uid = stable_circuit_uuid("kitchen-circuit-1")
    assert len(uid) == 32
    assert "-" not in uid


def test_stable_circuit_uuid_is_deterministic() -> None:
    a = stable_circuit_uuid("kitchen-circuit-1")
    b = stable_circuit_uuid("kitchen-circuit-1")
    assert a == b


def test_stable_circuit_uuid_differs_for_different_inputs() -> None:
    assert stable_circuit_uuid("a") != stable_circuit_uuid("b")
