"""Do our device IDs follow the pattern real SPAN firmware publishes?

The comparator aligns devices by declared ``type::name`` and never by instance
id — deliberately, because the two producers derive ids differently, so an
id-keyed diff reports every device as a mismatch and nothing useful. The cost of
that choice is that device ids are outside what it measures, and a producer
diverging from firmware would do so without the comparator noticing.

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

Device ids are a per-producer pattern check, not a pairwise one, so the two rows
need not describe the same panel. The ``panelbench`` row is PanelBench's own
config, ``configs/default_MAIN_40.yaml``; the ``reference`` row is upstream's
shipped example, published by upstream. They cannot be paired in the example
cell either: PanelBench's import runs the example as a clone, which serves its
own panel serial and ids its circuits by uuid. Both producers id a PV inverter
``<panel>-<serial or model>``, with the feeding circuit appended when a panel has
two or more, and both conform to ``<proxier-id>-<identifier>``.

Both rows are recorded, in one file, so a divergence is visible in the artifact
rather than only in this docstring. The reference's entries are not a demand on
upstream — its ids come from an example definition, which names its battery,
drives, lugs and circuits by readable slugs, not from the emitter contract — but
movement there is worth seeing, because it is where ours came from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from panelbench.emitter_adapter.wire_capture import as_capture, capture_retained

from .against_spec import device_id_findings
from .comparator import PANELBENCH_CONFIG, reference_example

BASELINE = Path(__file__).parent / "fixtures" / "device_id_baseline.json"


@pytest.mark.asyncio
async def test_device_ids_match_the_recorded_baseline() -> None:
    """Fails on movement in either direction, like the other baselines.

    An id corrected to the firmware pattern should shrink this file. A new
    off-pattern device should fail rather than arrive unnoticed.
    """
    actual = {
        "panelbench": device_id_findings(as_capture(await capture_retained(PANELBENCH_CONFIG))),
        "reference": device_id_findings(reference_example()),
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
