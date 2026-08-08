"""Do our device IDs follow the pattern real SPAN firmware publishes?

The comparator aligns devices by declared ``type::name`` and never by instance
id — deliberately, because the reference hashes ids with sha256 while panelbench
uses uuid5, so an id-keyed diff reports every device as a mismatch and nothing
useful. The cost of that choice is that device ids are outside what it measures,
and both producers inherited the same ids from the same example code. They
diverge from firmware *identically*, so the comparator reports parity on every
one of them.

The patterns come from the migration guide's Device ID Stability table:

===========  ==========================================
Panel        ``<panel-serial>``
Lugs         ``<panel-serial>-lugs-{up,dn}``
MID          ``<bess-id>-mid`` or ``<panel-serial>-mid``
Circuit      ``<circuit-uuid>``
BESS/PV/EVSE ``<proxier-id>-<identifier>`` (proxied)
===========  ==========================================

This matters beyond tidiness. A Home Assistant entity's ``unique_id`` derives
from the device id, and entity survival across the firmware upgrade is the
acceptance criterion the whole fidelity effort exists to make measurable. A
consumer validated against ids no panel ever publishes has not been validated on
the axis that decides whether a user's history survives.

Both producers are recorded, in one file, so the shared divergence is visible in
the artifact rather than only in this docstring. The reference's entry is not a
demand on upstream — its ids come from an example script, not from the emitter
contract — but movement there is worth seeing, because it is where ours came
from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .against_spec import device_id_findings
from .comparator import PANELBENCH_CONFIG, capture_panelbench, capture_reference

BASELINE = Path(__file__).parent / "fixtures" / "device_id_baseline.json"


@pytest.mark.asyncio
async def test_device_ids_match_the_recorded_baseline() -> None:
    """Fails on movement in either direction, like the other baselines.

    An id corrected to the firmware pattern should shrink this file. A new
    off-pattern device should fail rather than arrive unnoticed.
    """
    actual = {
        "panelbench": device_id_findings(await capture_panelbench(PANELBENCH_CONFIG)),
        "reference": device_id_findings(capture_reference(PANELBENCH_CONFIG)),
    }
    expected = json.loads(BASELINE.read_text())

    assert actual == expected, (
        "device ids moved relative to the documented firmware patterns.\n"
        f"{json.dumps(actual, indent=2, sort_keys=True)}\n\n"
        f"If an id was corrected, remove its line from {BASELINE.name}."
    )


def test_a_panel_whose_id_is_not_its_serial_is_flagged() -> None:
    """Guards the premise every other pattern rests on.

    All patterns are expressed relative to the panel serial. If that serial were
    read from the panel's *device id*, the panel check would compare the id to
    itself and pass for any value, and the lugs and proxied-DER checks would
    inherit whatever the panel happened to be called.

    Both producers set the panel's device id equal to its published serial, so
    no real capture can tell the two sources apart. This is a synthetic one that
    can: it fails if `panel_serial` is ever changed to read the device id.
    """
    devices = {
        "not-the-serial": {
            "$description": json.dumps(
                {"type": "energy.ebus.device.distribution-enclosure", "name": "Panel"}
            ),
            "info/serial-number": "sim-40t-001",
        }
    }

    findings = device_id_findings(devices)

    assert findings == {
        "energy.ebus.device.distribution-enclosure::Panel": (
            "not-the-serial is not <panel-serial>"
        )
    }
