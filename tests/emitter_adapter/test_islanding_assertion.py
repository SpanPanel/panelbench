"""What a write to `shed/asserted-islanding-state` does on a PanelBench panel.

PanelBench registers no handler of its own: it passes the emitter an empty
`SetterRegistry`, so the emitter's built-in handler decides. That handler follows
a SPAN panel, accepting `ON_GRID` or `OFF_GRID` only while the panel's link to
some battery is not known to be healthy. Home Assistant's dominant-power-source
control writes this property, so the rule reaches a user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.runtime import (
    CloneRuntime,
    bess_config_from_engine,
    update_bess_config_live,
)
from tests._helpers import DEFAULT_CONFIG, clone, default_config, published, write_config

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_ASSERTION = "shed/asserted-islanding-state"
_LINK = "status/communication-state"


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


async def test_an_assertion_is_accepted_while_the_battery_link_is_lost() -> None:
    runtime, recorder = await clone(DEFAULT_CONFIG)
    await emitter_runtime.publish_tick(runtime)

    runtime.engine.set_bess_link("LOST")
    await emitter_runtime.publish_tick(runtime)
    _assert(runtime, "OFF_GRID")
    await emitter_runtime.publish_tick(runtime)

    battery = bess_config_from_engine(runtime.engine)
    assert battery is not None
    assert published(recorder, battery.instance_id, _LINK) == "LOST"
    assert published(recorder, runtime.engine.serial_number, _ASSERTION) == "OFF_GRID"


async def test_a_link_set_without_a_battery_is_not_sent(tmp_path: Path) -> None:
    config = default_config()
    config["bess"] = {"enabled": False}
    runtime, recorder = await clone(write_config(tmp_path / "no_battery.yaml", config))

    runtime.engine.set_bess_link("LOST")
    # Naming a battery the emitter was not configured with would raise
    # EmitterStateError, so without one the tick carries no link at all.
    assert emitter_runtime._bess_communication(runtime.engine, "LOST") == {}
    await emitter_runtime.publish_tick(runtime)

    assert not [topic for topic in recorder.retained if topic.endswith(f"/{_LINK}")]


async def test_a_battery_disabled_mid_run_stops_receiving_the_link() -> None:
    runtime, recorder = await clone(DEFAULT_CONFIG)
    battery = bess_config_from_engine(runtime.engine)
    assert battery is not None
    runtime.engine.set_bess_link("LOST")
    await emitter_runtime.publish_tick(runtime)
    assert published(recorder, battery.instance_id, _LINK) == "LOST"

    update_bess_config_live(runtime, {"enabled": False})

    # The link is keyed from the live config, so the disabled battery gets nothing.
    # The emitter keeps the battery it was built with, and one left out of the tick
    # reads as OK.
    assert emitter_runtime._bess_communication(runtime.engine, "LOST") == {}
    await emitter_runtime.publish_tick(runtime)
    assert published(recorder, battery.instance_id, _LINK) == "OK"
