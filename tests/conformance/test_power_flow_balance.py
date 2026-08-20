"""The four `power-flows` values are one node balance: `grid + pv + battery == site`.

Kirchhoff at the enclosure node. All four are published positive when power flows
*into* the metered thing, per the default reference direction
`capabilities/meter.md` 0.2 establishes, which is what makes them one balance
rather than four readings that happen to be related. A term published in its own
device's frame inverts, and the sum misses by twice that term.

**What this catches, proved rather than asserted.** Negating `power-flows/pv` in
the emitter's wire resolver fails it with a residual of `-2 x pv`, which is the
signature the failure message names. So it guards the assembly *between* the
resolver and the wire — a negation applied while publishing, a term rewired to
the wrong source — which is exactly the seam the emitter's own tests cannot see.

**What it does not catch, which matters more.** It would *not* have caught the
four sign-frame defects fixed in the emitter during August 2026. panelbench pins
`ebus-panel-sim` 0.5.1, which predates all four, and this test passes against it:
the identity already holds at the operating point a capture happens to take.

That is not a flaw to fix here, it is the shape of the instrument. A wire capture
observes one operating point and cannot choose it — `noise_factor` and a running
clock move every term run to run, so consecutive captures differ by thousands of
watts. The emitter tests the same identity at the *resolver*, where the inputs
are settable, across nine parametrised cases (`test_power_flows_sum_to_zero`:
battery charging and discharging, grid up and down, with and without a battery).
Nine chosen points discriminate; one arbitrary point does not.

So this is a smoke check on the full assembly, not a replacement for that. Read a
failure here as "something between the resolver and the topic inverted a term",
and do not read a pass as "the frames are right".

**Why it may assert values where `test_wire_capture.py` may not.** That module
holds the line that structure is asserted and values never are, because the
values move every run. This is the same exception `test_enum_values.py` claims
for enum membership, in a different form: the individual flows move under noise
and the identity between them does not. Kirchhoff does not care what the load is.

It runs against a **fresh** capture rather than `golden_wire.json` because that
fixture is committed, so it would go on agreeing with itself after the emitter
regressed.
"""

from __future__ import annotations

import pathlib

import pytest
import pytest_asyncio

from panelbench.emitter_adapter.wire_capture import capture

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
_CONFIG = _REPO / "configs" / "default_MAIN_40.yaml"

_NODE = "power-flows"
_TERMS = ("grid", "pv", "battery", "site")

_TOLERANCE = 1e-6
"""Absolute watts the balance may miss by.

Generous against float error and mean against a real defect. The terms are sums
of O(10^4) W readings, so double-precision error lands around 1e-9; the frame
error these fixes corrected missed by `2 x site`, which is O(10^4). There is no
plausible defect hiding between the two.
"""


def _flows(captured: dict[str, dict[str, str]]) -> dict[str, dict[str, float]]:
    """Every device's `power-flows` values, by device id.

    Keyed off the published topic rather than off a device type, because the
    question is about whatever publishes the capability — a second enclosure on
    the tree would have to balance too, and finding it by type would need this
    test to know which types can carry the node.
    """
    found: dict[str, dict[str, float]] = {}
    for device_id, body in captured.items():
        values = {
            topic.split("/", 1)[1]: float(payload)
            for topic, payload in body.items()
            if topic.startswith(f"{_NODE}/") and not topic.startswith("$")
        }
        if values:
            found[device_id] = values
    return found


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def flows() -> dict[str, dict[str, float]]:
    return _flows(await capture(_CONFIG))


@pytest.mark.asyncio(loop_scope="module")
async def test_something_publishes_power_flows(flows: dict[str, dict[str, float]]) -> None:
    """Guard the premise: an empty capture would make every assertion below vacuous.

    The failure mode this prevents is the quiet one — a rename or a config change
    that stops the node being published, leaving a green suite that is testing
    nothing.
    """
    assert flows, f"no device published a {_NODE}/ topic; the balance below would assert nothing"


@pytest.mark.asyncio(loop_scope="module")
async def test_every_publisher_carries_all_four_terms(flows: dict[str, dict[str, float]]) -> None:
    """A balance missing a term is not a balance, and would silently pass as one."""
    for device_id, values in flows.items():
        missing = set(_TERMS) - set(values)
        assert not missing, f"{device_id} publishes {_NODE} without {sorted(missing)}"


@pytest.mark.asyncio(loop_scope="module")
async def test_the_four_flows_sum_to_zero(flows: dict[str, dict[str, float]]) -> None:
    """`grid + pv + battery + site == 0`, for every device that publishes the node.

    Sum to **zero**, not "three sum to the fourth". All four are published in one
    reference direction — positive when power flows *into* the metered thing, per
    the default `capabilities/meter.md` 0.2 establishes — so the terms entering
    the node and the term leaving it cancel. `site` is not a total, it is the
    fourth flow.

    Getting this backwards is not hypothetical: an earlier version of this test
    asserted `grid + pv + battery == site`, which is the *old* frame. It passed
    against an emitter publishing three terms in the meter frame and failed
    against the one that had been fixed — the precise inverse of its purpose.
    The residual it reported was `-2 x site`, which is the signature named below.

    Iterated over every publisher rather than the panel alone: a second enclosure
    on the tree would have to balance too, and nothing here needs to know which
    device types can carry the capability to check that.
    """
    for device_id, values in flows.items():
        terms = {name: values[name] for name in _TERMS}
        residual = sum(terms.values())

        assert abs(residual) <= _TOLERANCE, (
            f"{device_id} {_NODE} does not sum to zero: "
            + " + ".join(f"{name}={value}" for name, value in terms.items())
            + f" = {residual}.\n"
            f"A residual near 2 x one term is that term published in its own device's frame "
            f"instead of the enclosure's; near 2 x site is the whole set inverted."
        )
