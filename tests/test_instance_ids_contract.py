"""The circuit-id derivation is a contract with the flat simulator, not a local detail.

The firmware-upgrade rehearsal stops the simulator and starts PanelBench on one
panel, carrying the config across. A circuit whose device id changed at the swap
reads as a new device to Home Assistant: its entities get new unique ids, and the
history on the old ones strands. So the two producers must derive the *same* id
from the same (panel serial, circuit id) pair, byte for byte.

The literal below is the derivation pinned as a value rather than as a formula.
The simulator's suite asserts the same literal for the same inputs, so a change
made on one side alone fails CI on both -- which is the whole point of writing it
out instead of recomputing it here from `uuid5`.
"""

from __future__ import annotations

from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid

# CROSS-REPO CONTRACT with the flat simulator (span/simulator). Do not change this
# literal without changing it there in the same breath; it is uuid5 of
# `a1b2c3d4-e5f6-7890-abcd-ef1234567890` over the name "sim-40t-001/solar_inverter",
# dashless.
SOLAR_INVERTER_ON_SIM_40T_001 = "be87c32bda4f5cd9abbf6d3995ae28c0"


def test_the_derivation_matches_the_simulators() -> None:
    assert stable_circuit_uuid("sim-40t-001", "solar_inverter") == SOLAR_INVERTER_ON_SIM_40T_001


def test_two_panels_sharing_a_circuit_id_get_different_device_ids() -> None:
    """The bug this scoping exists to prevent, stated as an assertion.

    `default_MAIN_32.yaml` and `default_MAIN_40.yaml` both define a circuit whose
    `id` is `solar_inverter`. Run together in one add-on they share a broker, so
    two equal device ids means two panels writing the same `ebus/5/<uuid>/...`
    topics and a consumer reading each circuit's meters as the two loads
    alternating.
    """
    assert stable_circuit_uuid("sim-32t-001", "solar_inverter") != stable_circuit_uuid(
        "sim-40t-001",
        "solar_inverter",
    )
