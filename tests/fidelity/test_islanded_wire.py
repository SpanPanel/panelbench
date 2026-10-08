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

PanelBench is the subject, run over its own config with the grid forced down
before the capture, as the dashboard's grid toggle forces it. That is the panel a
user islands, and it runs the same emitter upstream's example does, so the branch
this exercises is the one this repo depends on.
"""

from __future__ import annotations

import pytest

from panelbench.emitter_adapter.wire_capture import as_capture, capture_retained

from .comparator import PANELBENCH_CONFIG, class_of

pytestmark = pytest.mark.asyncio


async def _islanded() -> dict[str, dict[str, str]]:
    return as_capture(await capture_retained(PANELBENCH_CONFIG, grid_online=False))


def _mid(devices: dict[str, dict[str, str]]) -> dict[str, str]:
    mids = [body for body in devices.values() if class_of(body) == "mid"]
    assert len(mids) == 1, f"expected exactly one MID in the capture, found {len(mids)}"
    return mids[0]


async def test_the_outage_actually_takes_the_panel_off_grid() -> None:
    """The precondition every other assertion here rests on.

    Held separately so that a capture that stops forcing the grid down fails
    *here*, naming the cause, rather than surfacing downstream as a
    grid-forming-entity that mysteriously stopped being a device id.
    """
    mid = _mid(await _islanded())

    assert mid["grid/islanding-state"] == "OFF_GRID"
    assert mid["grid/grid-state"] == "DOWN"


async def test_the_islanded_grid_forming_entity_names_a_device_on_the_wire() -> None:
    """The pin-regression guard, and the reason this file exists.

    The catalog defines the property as ``"GRID"`` when grid-tied "or the Homie
    device ID of the grid-forming device … when islanded". Asserting the literal
    id would pass for any other plausible constant — ``"BESS"`` included, on a
    day someone decides that reads better — so this asserts the published value
    resolves to a device that has a ``$description`` in the same capture, which
    is the property a consumer actually needs.
    """
    devices = await _islanded()
    mid = _mid(devices)

    former = mid["grid/grid-forming-entity"]

    assert former != "GRID", "the panel is islanded; the grid is not forming it"
    assert former in devices, (
        f"grid-forming-entity is {former!r}, which is not a device on the wire. "
        "The catalog asks for the grid-forming device's Homie id, not its class. "
        "If the emitter pin moved, it moved to one without this fix."
    )
