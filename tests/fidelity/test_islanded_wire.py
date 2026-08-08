"""The islanded branch of the MID, which no other test in this repo reaches.

Every other capture here is grid-tied, so ``grid/grid-forming-entity`` reads
``"GRID"`` and the interesting branch — the one that has to name a *device* —
never runs. The emitter published the class name ``"BESS"`` there for its whole
life, and nothing in this repo could have noticed: the comparator comes to that
property with both producers running one emitter, so an emitter defect is parity
by identity, and the conformance checker sees a ``string`` datatype where any
value is legal.

Two blindfolds at once, which is why this is a wire assertion rather than a
comparison. What it really guards is the *pin*: repoint the dependency at an
emitter without the fix and this fails, where every other test in the suite
would stay green.

The reference producer is the subject because it is the one driven by the
config's ``ticks``. panelbench's grid state comes from its engine, whose default
is online and whose setter is a runtime control, so its capture cannot reach this
branch from a config file at all. Both run the same emitter, so the branch this
exercises is the one this repo depends on either way.
"""

from __future__ import annotations

import pytest

from .comparator import PANELBENCH_CONFIG, capture_reference, class_of


@pytest.fixture(scope="module")
def mid() -> dict[str, str]:
    devices = capture_reference(PANELBENCH_CONFIG)
    mids = [body for body in devices.values() if class_of(body) == "mid"]
    assert len(mids) == 1, f"expected exactly one MID in the capture, found {len(mids)}"
    return mids[0]


def test_the_outage_tick_actually_takes_the_panel_off_grid(mid: dict[str, str]) -> None:
    """The precondition every other assertion here rests on.

    Held separately so that dropping the off-grid tick from the config fails
    *here*, naming the cause, rather than surfacing downstream as a
    grid-forming-entity that mysteriously stopped being a device id.
    """
    assert mid["grid/islanding-state"] == "OFF_GRID"
    assert mid["grid/grid-state"] == "DOWN"


def test_the_islanded_grid_forming_entity_names_a_device_on_the_wire() -> None:
    """The pin-regression guard, and the reason this file exists.

    The catalog defines the property as ``"GRID"`` when grid-tied "or the Homie
    device ID of the grid-forming device … when islanded". Asserting the literal
    id would pass for any other plausible constant — ``"BESS"`` included, on a
    day someone decides that reads better — so this asserts the published value
    resolves to a device that has a ``$description`` in the same capture, which
    is the property a consumer actually needs.
    """
    devices = capture_reference(PANELBENCH_CONFIG)
    body = next(b for b in devices.values() if class_of(b) == "mid")

    former = body["grid/grid-forming-entity"]

    assert former != "GRID", "the panel is islanded; the grid is not forming it"
    assert former in devices, (
        f"grid-forming-entity is {former!r}, which is not a device on the wire. "
        "The catalog asks for the grid-forming device's Homie id, not its class. "
        "If the emitter pin moved, it moved to one without this fix."
    )
