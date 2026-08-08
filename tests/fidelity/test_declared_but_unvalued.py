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
