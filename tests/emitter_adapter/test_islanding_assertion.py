"""What a write to `shed/asserted-islanding-state` does on a PanelBench panel.

PanelBench registers no handler of its own: it passes the emitter an empty
`SetterRegistry`, so the emitter's built-in handler decides. That handler follows
a SPAN panel, accepting `ON_GRID` or `OFF_GRID` only while the panel's link to
some battery is not known to be healthy. Home Assistant's dominant-power-source
control writes this property, so the rule reaches a user.
"""

from __future__ import annotations

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.runtime import CloneRuntime
from tests._helpers import DEFAULT_CONFIG, clone, published

pytestmark = pytest.mark.asyncio

_ASSERTION = "shed/asserted-islanding-state"


def _assert(runtime: CloneRuntime, value: str) -> None:
    handler = runtime.setters.get("panel", _ASSERTION)
    assert handler is not None, "the emitter registers a default islanding handler"
    handler("panel", runtime.engine.serial_number, _ASSERTION, value)


async def test_an_assertion_is_ignored_while_the_battery_link_is_healthy() -> None:
    runtime, recorder = await clone(DEFAULT_CONFIG)
    await emitter_runtime.publish_tick(runtime)

    _assert(runtime, "OFF_GRID")
    await emitter_runtime.publish_tick(runtime)

    assert published(recorder, runtime.engine.serial_number, _ASSERTION) == "NONE"
