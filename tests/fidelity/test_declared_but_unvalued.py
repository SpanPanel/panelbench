"""What both producers declare and neither ever publishes.

`test_upstream_parity.py` is a *differential* instrument: it compares published
topics between two producers. That makes it blind in one direction by
construction — when both sides declare a property and neither publishes a value,
the comparison reports parity, and the gap is invisible precisely because it is
shared.

The conformance report cannot see these either. Its omissions are computed from
declarations, so a property that *is* declared is not an omission no matter how
permanently absent its value.

The MID's `info/serial-number` is the worked example. Both producers declare it,
neither publishes a value for it, and both instruments call that fine.

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

The example cell drives this: both producers publish the same panel, upstream's
shipped definition, driven by upstream's ticks. Lines are keyed by
``comparator.role_key``: a circuit's carry the breaker spaces after the name, so
the two commissioned PV circuits, which share a name, cannot hide behind each
other, and the battery's, the MID's and each inverter's carry where the device
hangs rather than its name.

Shrinking this file is not the goal — being right about each line is. Membership
was audited against the specification and SPAN's r202633 topic reference, and
what remains is there for four different reasons:

  `connection/feeds-device-*` on a mixed-load circuit is *correct* absence, not a
  gap. The catalog omits the triple "when mixed-load with no commissioned
  downstream device", and says an unpublished property is itself the "unknown"
  signal; r202633 states that mixed-load and unsurveyed circuits publish no
  `connection` records at all. Both producers value these on the four circuits
  that feed a device (both SPAN Drives and both commissioned PV circuits) and
  nowhere else, which is the documented behaviour. These lines should never leave
  this file.

  An EVSE's `config/user-max-charge-current` is *correct* absence too. The example
  names firmware `example/v0.1.0`, which names no SPAN release and so takes the
  current conventions, and from release 202639 a drive leaves the property
  unpublished until a user sets a limit. These lines leave if the example comes to
  name a release before 202639, where the commissioned maximum is published until
  then.

  `connection/feeds-*` on both lugs and `fed-by-*` on the downstream lugs are a
  genuinely open question, and the one place a future value might land. The spec
  says downstream lugs *typically* populate `feeds-*` — but its own live worked
  example records that current SPAN firmware does not, and that only the
  receiving end of an inter-panel link is populated. Neither producer has a
  sub-enclosure to point at. Valuing them would mean modelling a topology neither
  producer has, on firmware behaviour nobody has observed.

  `info/*` on the battery, the MID and both PV inverters is a fact about upstream's
  example, not about either producer. The emitter declares a device class's whole
  `info` catalog and values only what the device's metadata names, and the example
  names no battery firmware, part number or serial, nothing for the MID beyond its
  vendor, and no inverter firmware or serial. PanelBench's import reads the
  example's tree, so it has nothing more to publish. PanelBench's own config names
  every one of these but an inverter serial. These lines leave when upstream's
  example names the values, or when the emitter stops declaring `info` properties
  a device has no value for. Carrying more identity through the clone does not
  remove them, since membership needs the reference to leave them unvalued too.

Two kinds of line have left:

  `panel status/wifi-ssid` was valued because the enclosure device model defines
  it (MAY), r202633 documents it as the MQTT successor to the panel's Wi-Fi REST
  endpoint, and consumers read the flat equivalent today — evidence about the
  panel, not about the emitter's mechanism.

  `connection/count` described a node aggregating several physical units behind
  one connection point. Nothing here aggregates, and no manifest key or tick input
  carries a count. Upstream stopped declaring it, so it left by the declaration
  going away, not by either producer inventing a value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .against_spec import declared_but_unvalued
from .comparator import example_pair

BASELINE = Path(__file__).parent / "fixtures" / "unvalued_by_both_baseline.json"


@pytest.mark.asyncio
async def test_the_shared_unvalued_declarations_match_the_recorded_baseline(
    tmp_path: Path,
) -> None:
    """Fails on movement in either direction, like the other baselines.

    A declaration gaining a value should shrink this file. A new declaration
    arriving without one should fail rather than pass silently, because the cost
    of that mistake lands on a consumer, not here.
    """
    reference, subject = await example_pair(tmp_path)

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
