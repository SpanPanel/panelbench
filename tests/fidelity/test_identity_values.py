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

Two of the recorded entries are intentional and must never be "fixed":

``info/serial-number`` on the minimal cell
    panelbench forces a simulated panel's serial to carry a ``sim-`` prefix
    (``clone.py:51``), idempotently, so a simulator can never present a serial
    that reads as real hardware. The reference has no such rule. Every proxied
    device's serial derives from the panel's, so one deliberate rule shows up on
    the panel and both EVSEs.

``info/firmware-version`` everywhere
    the two producers have different placeholder defaults, ``example/v0.1.0``
    against ``sim/v0.1.0``. Same class as the PV model: a naming difference, not
    a fidelity defect.

The EVSE entry is the one worth acting on, and it is only visible because the
placeholder difference dragged it into view. The reference reads an EVSE's
firmware version from ``panel_config`` (``run_forty_tab_minimal.py:265``) while
panelbench reads it from that EVSE's own config block
(``spec_generator.py:284``). Set ``panel_config.firmware_version`` and the
reference's EVSEs follow it; ours do not. Ours is the more defensible source, so
this is recorded as a divergence rather than chased as a bug.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .comparator import (
    PANELBENCH_CONFIG,
    REFERENCE_CONFIG,
    ValueReport,
    capture_panelbench,
    capture_reference,
    compare_identity_values,
)

BASELINE = Path(__file__).parent / "fixtures" / "identity_value_baseline.json"

CELLS = (("minimal", REFERENCE_CONFIG), ("rich", PANELBENCH_CONFIG))

_by_name = pytest.mark.parametrize(("name", "config"), CELLS, ids=[c[0] for c in CELLS])


@_by_name
@pytest.mark.asyncio
async def test_identity_values_match_the_recorded_baseline(name: str, config: Path) -> None:
    """Fails on movement in either direction, like the other baselines."""
    report = compare_identity_values(capture_reference(config), await capture_panelbench(config))
    expected = json.loads(BASELINE.read_text())[name]

    assert report.as_baseline() == expected, (
        f"identity values moved for the {name} config.\n"
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


def test_a_value_only_one_producer_publishes_is_left_to_the_parity_baseline() -> None:
    """Held here so the two instruments cannot both claim the same finding.

    A key present on one side only is a structural gap. If it were reported as a
    value difference too, closing it would mean editing two baselines, and one of
    them would eventually be forgotten.
    """
    report = compare_identity_values(
        _capture_of({"info/serial-number": "SIM-BESS-001-mid", "info/model": "SPAN MID"}),
        _capture_of({"info/serial-number": "SIM-BESS-001-mid"}),
    )

    assert report == ValueReport()


def _capture_of(properties: dict[str, str]) -> dict[str, dict[str, str]]:
    """One synthetic MID, keyed the way ``_regroup`` keys a real capture."""
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
