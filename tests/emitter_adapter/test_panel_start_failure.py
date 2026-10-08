"""A panel that fails part-way through starting leaves no link behind.

A broker link supervises itself, so a link nobody closes reconnects for the life
of the process under the panel's client id. When the panel is fixed and started
again, the two take the id from each other, and Home Assistant watches the panel's
state flap between lost and ready.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import TYPE_CHECKING

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.panel import PanelInstance

if TYPE_CHECKING:
    from tests.emitter_adapter._fake_aiomqtt import FakeBroker

_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "configs" / "default_MAIN_40.yaml"


@pytest.mark.asyncio
async def test_a_panel_whose_first_tick_fails_closes_its_link(
    fake_broker: FakeBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(_runtime: emitter_runtime.CloneRuntime) -> object:
        raise RuntimeError("the emitter refused the tick")

    monkeypatch.setattr(emitter_runtime, "publish_tick", refuse)
    panel = PanelInstance(_CONFIG)

    with pytest.raises(RuntimeError, match="refused the tick"):
        await panel.start()

    [client] = fake_broker.clients
    assert client.exited, "the panel's broker connection was left open"
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("mqtt-link")]
    assert panel.runtime is None
    assert not panel.is_running
