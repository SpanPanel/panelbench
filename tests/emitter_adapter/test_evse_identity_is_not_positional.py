"""A drive's identity belongs to the drive, not to its position in a list.

`info/serial-number` is what a consumer keys an EVSE on: the proxy model says a
proxied device id is not stable across the proxy-to-native transition and that
"consumers that need cross-transition stable identity use `info/serial-number`". The
integration builds its Home Assistant device identifier from it, so the serial is the
thread a drive's entities, history and automations hang from.

The fallback derivation appends `-<idx>` taken from the order `device_type: evse`
circuits are walked. Swapping two drives in a config therefore re-keys both: Home
Assistant builds new devices, the old ones strand with their history, and nothing
raises. `schema-1`'s keying function names inventing an ordinal as the thing it
exists to avoid; the fallback invents one on the producer side.

This is not a flat-transition problem and does not retire with flat -- it happens to
a pure v1.0 user with no flat anywhere in the picture. The fix is for a circuit to
carry its own serial, which is what these tests hold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from panelbench.emitter_adapter.spec_generator import build_manifest

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig


def _profile(circuits: list[dict[str, Any]]) -> SimulationConfig:
    return cast(
        "SimulationConfig",
        {
            "panel_config": {
                "serial_number": "sim-40t-001",
                "total_tabs": 40,
                "main_size": 200,
            },
            "circuit_templates": {
                "drive": {
                    "energy_profile": {"mode": "consumer", "typical_power": 7200.0},
                    "device_type": "evse",
                    "priority": "OFF_GRID",
                },
            },
            "circuits": circuits,
        },
    )


def _garage(**extra: object) -> dict[str, Any]:
    return {"id": "drive_garage", "name": "Garage", "template": "drive", "tabs": [32], **extra}


def _driveway(**extra: object) -> dict[str, Any]:
    return {"id": "drive_driveway", "name": "Driveway", "template": "drive", "tabs": [35], **extra}


def _serials_by_name(profile: SimulationConfig) -> dict[str, str]:
    return {
        inst.display_name: inst.metadata["serial-number"]
        for inst in build_manifest(profile).of_class("evse")
    }


def test_a_circuit_serial_survives_reordering() -> None:
    """The property that matters, asserted as an invariant over order.

    Two orderings of the same two drives must produce the same serial for each drive.
    Asserting the mapping rather than a literal is deliberate: a literal would pass
    for a derivation that is stable *and* one that is stable only by coincidence for
    the order the test happens to write first.
    """
    forward = _serials_by_name(
        _profile([_garage(serial_number="drive-0001"), _driveway(serial_number="drive-0002")])
    )
    reversed_ = _serials_by_name(
        _profile([_driveway(serial_number="drive-0002"), _garage(serial_number="drive-0001")])
    )

    assert forward == reversed_ == {"Garage": "drive-0001", "Driveway": "drive-0002"}


def test_without_a_circuit_serial_identity_is_still_positional() -> None:
    """The known gap, pinned rather than left implicit.

    The fallback stays positional on purpose while the flat simulator exists: flat has
    no per-circuit serial, and the two producers have to agree on this string or the
    upgrade rehearsal compares two different chargers. So this documents a limitation
    that is live, not a bug being tolerated silently.

    When flat retires, the shipped configs should adopt per-circuit serials and this
    expectation should invert -- at which point this test is the thing that notices.
    """
    forward = _serials_by_name(_profile([_garage(), _driveway()]))
    reversed_ = _serials_by_name(_profile([_driveway(), _garage()]))

    assert forward != reversed_, (
        "the positional fallback is expected to re-key on reorder; if it no longer "
        "does, per-circuit identity has become the default and this test should be "
        "replaced by the invariant above"
    )


def test_the_fallback_matches_what_the_flat_simulator_publishes() -> None:
    """Transitional. Delete with the flat simulator.

    Flat 1.0.16 made the drive serial its Homie node id, which forced the serial
    lower-case: a node id is a topic level, and Homie 5 allows only `a`-`z`, `0`-`9`
    and `-` there. Flat could not move to meet this side, so this side moved.

    The literal is the point. `info/serial-number` is the only identifier common to
    both schemas, so if these two strings drift apart the upgrade silently produces
    two Home Assistant devices for one physical drive -- which is exactly what it did
    before 1.0.16, and it showed up as a duplicate device rather than a failure.
    """
    serials = _serials_by_name(_profile([_garage(), _driveway()]))

    assert serials["Garage"] == "sim-evse-sim-40t-001"
    assert serials["Driveway"] == "sim-evse-sim-40t-001-2"
