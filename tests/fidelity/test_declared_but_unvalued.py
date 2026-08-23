"""What both producers declare and neither ever publishes.

`test_upstream_parity.py` is a *differential* instrument: it compares published
topics between two producers. That makes it blind in one direction by
construction — when both sides declare a property and neither publishes a value,
the comparison reports parity, and the gap is invisible precisely because it is
shared.

The conformance report cannot see these either. Its omissions are computed from
declarations, so a property that *is* declared is not an omission no matter how
permanently absent its value.

`connection/count` is the worked example. Every circuit declares it, neither
producer ever publishes it, and both instruments call that fine.

This matters to a consumer because entities are built from `$description`. A
declared property with no retained value is an entity that never receives a
state — which reaches a user as an "unknown" that never resolves, not as a
missing entity they would notice.

Scope is deliberately non-overlapping: only the *intersection* is baselined here.
Declarations panelbench alone fails to value are already reported by the
comparator, and bookkeeping them twice would mean two files to edit when one gap
closes.

That disjointness is structural rather than something to assert. A parity gap
requires the reference to publish a value; membership here requires it not to.
No input can put an entry in both sets, so a test for it would be one that cannot
fail — which reads as a guarantee while providing none.

The rich config drives this rather than the minimal one: it strictly dominates,
carrying every device class the minimal config has and 25 more circuits, so it
declares a superset of the properties.

Shrinking this file is not the goal — being right about each line is. Membership
was audited against the specification and SPAN's r202633 topic reference, and
what remains is there for three different reasons:

  `connection/feeds-device-*` on a mixed-load circuit is *correct* absence, not a
  gap. The catalog omits the triple "when mixed-load with no commissioned
  downstream device", and says an unpublished property is itself the "unknown"
  signal; r202633 states that mixed-load and unsurveyed circuits publish no
  `connection` records at all. Panelbench values these on the three DER-feeding
  circuits and nowhere else, which is the documented behaviour. These lines
  should never leave this file.

  `connection/count` describes a node aggregating several physical units behind
  one connection point. Nothing here aggregates — every DER is its own device —
  and no manifest key or tick input exists to carry a count. It is documented
  only in the schema *superset* a panel returns from `/homie/schema`, which is
  explicitly a superset rather than an observation, and the sole worked example
  is hypothetical. Upstream has since removed the declaration outright, so these
  lines are expected to leave this file by the declaration going away, not by
  panelbench inventing a value.

  `connection/feeds-*` on both lugs and `fed-by-*` on the downstream lugs are a
  genuinely open question, and the one place a future value might land. The spec
  says downstream lugs *typically* populate `feeds-*` — but its own live worked
  example records that current SPAN firmware does not, and that only the
  receiving end of an inter-panel link is populated. Panelbench also has no
  sub-enclosure in any config to point at. Valuing them would mean modelling a
  topology this producer does not have, on firmware behaviour nobody has
  observed.

`panel status/wifi-ssid` is the line that left. It was valued because the
enclosure device model defines it (MAY), r202633 documents it as the MQTT
successor to the panel's Wi-Fi REST endpoint, and consumers read the flat
equivalent today — evidence about the panel, not about the emitter's mechanism.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .against_spec import declared_but_unvalued
from .comparator import PANELBENCH_CONFIG, capture_panelbench, capture_reference

BASELINE = Path(__file__).parent / "fixtures" / "unvalued_by_both_baseline.json"


@pytest.mark.asyncio
async def test_the_shared_unvalued_declarations_match_the_recorded_baseline() -> None:
    """Fails on movement in either direction, like the parity baseline.

    A declaration gaining a value should shrink this file. A new declaration
    arriving without one should fail rather than pass silently, because the cost
    of that mistake lands on a consumer, not here.
    """
    reference = capture_reference(PANELBENCH_CONFIG)
    subject = await capture_panelbench(PANELBENCH_CONFIG)

    both = declared_but_unvalued(subject) & declared_but_unvalued(reference)
    expected = set(json.loads(BASELINE.read_text()))

    appeared = sorted(both - expected)
    resolved = sorted(expected - both)
    assert both == expected, (
        "the set of declarations neither producer values moved.\n"
        f"  newly unvalued: {appeared}\n"
        f"  now valued:     {resolved}\n\n"
        f"If a value arrived, remove those lines from {BASELINE.name}."
    )
