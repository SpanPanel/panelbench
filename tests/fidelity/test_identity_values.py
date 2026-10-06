"""Do the two producers agree on *what each device says it is*?

The structural comparator next door aligns devices by declared ``type::name``
and diffs key sets. That leaves identity payloads entirely unmeasured: a device
publishing the wrong serial, model, or firmware version is structurally perfect,
because the property is present on both sides and only its value is wrong.

This was found by mutation rather than by review. Changing the MID's published
serial to a deliberate nonsense value left the whole fidelity and conformance
suite green — 47 passed — because no instrument compared a payload to a payload.
The values were already in the capture; nothing looked at them.

Scope is the ``info`` node and nothing else, which is a claim about the
specification rather than a convenience: ``info`` is catalog-declared and its
properties resolve from ``DeviceInstance.metadata``, so it is exactly the surface
a manifest builder decides. Every other node resolves from tick physics, where
the two producers are *supposed* to differ.

Only the example cell is measured. The PanelBench cell is deliberately absent:
its reference is upstream's reading of PanelBench's own published tree, so its
``info`` values are PanelBench's values read back, and comparing them would
compare PanelBench with a copy of itself.

The baseline's one entry, the panel's ``info/serial-number``, is intentional and
must never be "fixed". The import runs the example as a clone, and a clone serves
``sim-<serial>-clone`` (``clone.make_clone_serial``): a simulator never presents a
serial that reads as real hardware, and a clone never collides with the panel it
copies.

Every other value both producers publish agrees: the panel's firmware and hardware
version, both inverters' and the battery's model and vendor, and each SPAN Drive's
serial and firmware. A drive's serial is copied verbatim onto the circuit that
feeds it, because the drive's device id is already scoped by the clone's own panel
id and cannot collide with the source's.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from .comparator import (
    Capture,
    ValueReport,
    compare_identity_values,
    example_pair,
)

BASELINE = Path(__file__).parent / "fixtures" / "identity_value_baseline.json"

CELLS = (("example", example_pair),)

_by_name = pytest.mark.parametrize(("name", "pair"), CELLS, ids=[c[0] for c in CELLS])


@_by_name
@pytest.mark.asyncio
async def test_identity_values_match_the_recorded_baseline(
    name: str, pair: Callable[[Path], Awaitable[tuple[Capture, Capture]]], tmp_path: Path
) -> None:
    """Fails on movement in either direction, like the other baselines."""
    reference, subject = await pair(tmp_path)
    report = compare_identity_values(reference, subject)
    expected = json.loads(BASELINE.read_text())[name]

    assert report.as_baseline() == expected, (
        f"identity values moved for the {name} cell.\n"
        f"{report.describe()}\n\n"
        f"If a divergence was closed, remove its entry from {BASELINE.name} under "
        f'"{name}". If one appeared, it is a producer regression.'
    )


def test_a_differing_identity_value_is_reported() -> None:
    """The instrument must be able to fail, on the case that motivated it.

    A real capture cannot demonstrate this: the producers agree on every
    identity value that is not already in the baseline, so a passing suite is
    equally consistent with an instrument that reports nothing at all.
    """
    report = compare_identity_values(
        _capture_of({"info/serial-number": "SIM-BESS-001-mid"}),
        _capture_of({"info/serial-number": "SIM-BESS-001-WRONG"}),
    )

    assert report.as_baseline() == {
        "energy.ebus.device.mid::Microgrid Interconnect Device": {
            "info/serial-number": ["SIM-BESS-001-mid", "SIM-BESS-001-WRONG"]
        }
    }


def test_physics_payloads_are_not_compared() -> None:
    """Pins the scope, which is the whole reason this can assert equality.

    Meter readings, state of charge and power flows differ between the producers
    by design. If they were compared, this instrument would report dozens of
    entries per run and the baseline would be noise no one reads.
    """
    report = compare_identity_values(
        _capture_of({"meter/active-power": "-1200.0", "soc/state-of-energy": "6.75"}),
        _capture_of({"meter/active-power": "417.3", "soc/state-of-energy": "9.10"}),
    )

    assert report == ValueReport()


def test_a_value_only_one_producer_publishes_is_left_to_structural_parity() -> None:
    """Held here so the two instruments cannot both claim the same finding.

    A key present on one side only is a structural gap. If it were reported as a
    value difference too, one gap would fail two instruments, and recording it
    while it is closed would mean editing two places, one of which would
    eventually be forgotten.
    """
    report = compare_identity_values(
        _capture_of({"info/serial-number": "SIM-BESS-001-mid", "info/model": "SPAN MID"}),
        _capture_of({"info/serial-number": "SIM-BESS-001-mid"}),
    )

    assert report == ValueReport()


def _capture_of(properties: dict[str, str]) -> dict[str, dict[str, str]]:
    """One synthetic MID, keyed the way ``as_capture`` keys a real capture."""
    return {
        "bess-mid": {
            "$description": json.dumps(
                {
                    "type": "energy.ebus.device.mid",
                    "name": "Microgrid Interconnect Device",
                }
            ),
            **properties,
        }
    }
