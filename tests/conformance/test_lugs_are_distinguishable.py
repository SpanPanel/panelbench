"""The two lugs devices must not publish the same meter.

`test_wire_capture.py` deliberately asserts structure and never values, because
noise and the clock move every reading. This file is the one value assertion worth
making, and it is safe for the same reason the others are not: it is *relational*.
Whatever the noise does to a number, it does to both devices, so "these two differ"
holds run to run while "this equals 1234.5" does not.

**Why it needs guarding.** `lugs-upstream` carries the service feed; `lugs-downstream`
carries feedthrough. A consumer tells them apart by `info/direction` and maps them to
different fields. When both publish identical meters, a correct mapping and a swapped
one produce identical output — so the discrimination is untestable, and grid power
cannot be distinguished from feedthrough by any test anyone can write. That is not
hypothetical: it was the state of this repo until the `placement` default was
corrected, and it survived a deliberate upstream/downstream swap in the consumer
without a single failure.

The mechanism is arithmetic rather than physics. Feedthrough is the sum over circuits
placed `downstream-of-lugs`, while the upstream aggregate covers all of them, so
placing *every* circuit downstream makes the subset the whole and the two coincide
exactly. The spec puts loads past the feedthrough in a sub-enclosure — their own
device with their own circuits — rather than in this panel's circuit list, so a
faithful single-enclosure config has no `downstream-of-lugs` circuit at all and the
downstream meter reads zero. Either way the pair stays distinguishable; this test
does not care which, only that they are.

Asserting "at least one property differs" rather than "all of them do" is deliberate:
on the first tick no time has elapsed, so both energy counters are legitimately 0.
"""

from __future__ import annotations

import pathlib

import pytest

from panelbench.emitter_adapter.wire_capture import capture

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
_CONFIG = _REPO / "configs" / "default_MAIN_40.yaml"

_UPSTREAM = "lugs-upstream"
_DOWNSTREAM = "lugs-downstream"
_METER = (
    "meter/active-power",
    "meter/current-a",
    "meter/current-b",
    "meter/imported-energy",
    "meter/exported-energy",
)


@pytest.mark.asyncio
async def test_the_two_lugs_devices_publish_different_meters() -> None:
    captured = await capture(_CONFIG)

    for device in (_UPSTREAM, _DOWNSTREAM):
        assert device in captured, f"{device} published nothing, so the pair cannot be compared"

    upstream = captured[_UPSTREAM]
    downstream = captured[_DOWNSTREAM]

    shared = [prop for prop in _METER if prop in upstream and prop in downstream]
    assert shared, f"neither lugs device published any of {_METER}"

    differing = [prop for prop in shared if upstream[prop] != downstream[prop]]

    assert differing, (
        "lugs-upstream and lugs-downstream published byte-identical meters on every "
        f"property ({shared}), so nothing can tell them apart and a consumer that "
        "swapped grid power for feedthrough would read the same. The usual cause is "
        "circuits placed downstream-of-lugs, which makes the feedthrough sum equal the "
        "whole panel — check the `placement` default in "
        "src/panelbench/emitter_adapter/spec_generator.py."
    )
