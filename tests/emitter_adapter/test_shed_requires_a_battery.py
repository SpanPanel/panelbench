"""A panel without a battery publishes no load-shed state.

`devices/distribution-enclosure.md` publishes `shed-forecast` "only when at
least one BESS is commissioned; omitted otherwise" (line 196), and `shed` under
the "same presence rule" (line 202), at the specification commit `.ebus-spec.json`
pins. Without a battery there is no off-grid runtime to forecast or shed against,
so a consumer offered either node would show a control and estimates that mean
nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from tests._helpers import clone, default_config, published, write_config

pytestmark = pytest.mark.asyncio

_SHED_NODES = ("shed", "shed-forecast")


async def test_a_panel_without_a_battery_declares_and_publishes_no_shed_nodes(
    tmp_path: Path,
) -> None:
    config = default_config()
    config["bess"] = {"enabled": False}
    runtime, recorder = await clone(write_config(tmp_path / "no_battery.yaml", config))
    await emitter_runtime.publish_tick(runtime)
    panel = runtime.engine.serial_number

    nodes = json.loads(published(recorder, panel, "$description"))["nodes"]
    values = [
        topic
        for topic in recorder.retained
        if topic.startswith(tuple(f"ebus/5/{panel}/{node}/" for node in _SHED_NODES))
    ]

    assert not [node for node in _SHED_NODES if node in nodes]
    assert values == []
