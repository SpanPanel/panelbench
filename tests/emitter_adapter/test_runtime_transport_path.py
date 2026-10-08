"""The production publishing path: aiomqtt behind a synchronous SDK transport.

``wire_capture`` injects a ``RecordingTransport``, which is synchronous and has
no queue, so every fidelity capture bypasses ``LoopBoundTransport`` entirely.
Nothing else exercised the branch of ``start_clone`` that builds one — the branch
a real panel always takes. These tests stand a fake broker client behind it so
the queue, the drain and the teardown ordering run for real.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import TYPE_CHECKING

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.transport import LoopBoundTransport
from panelbench.engine import DynamicSimulationEngine
from tests._helpers import relay_opened_by_command, settable_relays

if TYPE_CHECKING:
    from tests.emitter_adapter._fake_aiomqtt import FakeBroker, FakeClient

_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "configs" / "default_MAIN_40.yaml"
_ROOT = "ebus/5/sim-40t-001"


async def _started(config: pathlib.Path) -> emitter_runtime.CloneRuntime:
    engine = DynamicSimulationEngine(config_path=config)
    await engine.initialize_async()
    return await emitter_runtime.start_clone(engine)


@pytest.mark.asyncio
async def test_the_tree_reaches_the_broker_through_the_queue(
    fake_broker: FakeBroker,
) -> None:
    """The whole point: a synchronous SDK publish path feeding an async client."""
    runtime = await _started(_CONFIG)
    assert isinstance(runtime.transport, LoopBoundTransport)
    await emitter_runtime.publish_tick(runtime)
    await runtime.transport.drain()

    client = fake_broker.clients[0]
    topics = [t for t, _, _, _ in client.published]

    assert client.entered
    assert len(topics) > 100, f"implausibly few topics for a 40-space panel: {len(topics)}"
    assert any(t.endswith("/$description") for t in topics)
    assert all(t.startswith("ebus/5/") for t in topics)

    await emitter_runtime.stop_clone(runtime)


@pytest.mark.asyncio
async def test_each_device_describes_itself_before_it_announces_ready(
    fake_broker: FakeBroker,
) -> None:
    """The ordering guarantee, asserted where it actually matters.

    A consumer that sees `$state=ready` first reads a `$description` that has not
    arrived. The transport's own test proves the queue preserves submission
    order; this proves the order submitted is the one Homie requires.
    """
    runtime = await _started(_CONFIG)
    assert isinstance(runtime.transport, LoopBoundTransport)
    await runtime.transport.drain()

    client = fake_broker.clients[0]
    seen_ready: set[str] = set()
    described: set[str] = set()
    for topic, payload, _qos, _retain in client.published:
        parts = topic.split("/")
        device = parts[2]
        if topic.endswith("/$description"):
            described.add(device)
        elif topic.endswith("/$state") and payload == b"ready":
            assert device in described, f"{device} announced ready before describing itself"
            seen_ready.add(device)

    assert seen_ready, "no device reached ready"
    await emitter_runtime.stop_clone(runtime)


@pytest.mark.asyncio
async def test_teardown_drains_before_closing_the_client(
    fake_broker: FakeBroker,
) -> None:
    """`stop` queues the root's final state and cannot flush it — the SDK's
    publish path is synchronous and the client behind it is not. Closing first
    would drop exactly the message that tells consumers the producer went away,
    and the fake raises rather than letting that pass silently.
    """
    runtime = await _started(_CONFIG)
    await emitter_runtime.publish_tick(runtime)

    await emitter_runtime.stop_clone(runtime, graceful=True)

    client = fake_broker.clients[0]
    assert client.exited, "the client was never closed"
    root_states = [
        payload for topic, payload, _q, _r in client.published if topic == f"{_ROOT}/$state"
    ]
    assert root_states, "the root never published a state"
    assert root_states[-1] != b"ready", (
        f"the retained root state after teardown is {root_states[-1]!r}"
    )


@pytest.mark.asyncio
async def test_a_relay_command_from_home_assistant_reaches_the_emitter(
    fake_broker: FakeBroker,
) -> None:
    """The defect, end to end: HA published `<circuit>/switch/relay/set OPEN` and
    PanelBench subscribed to it, then never read the message."""
    runtime = await _started(_CONFIG)
    assert isinstance(runtime.transport, LoopBoundTransport)
    await runtime.transport.drain()
    circuit = settable_relays(runtime)[0]

    fake_broker.send(f"ebus/5/{circuit}/switch/relay/set", b"OPEN")

    await relay_opened_by_command(runtime, circuit)
    await emitter_runtime.stop_clone(runtime)


@pytest.mark.asyncio
async def test_a_reconnect_restores_the_subscriptions_then_republishes_the_tree(
    fake_broker: FakeBroker,
) -> None:
    """What the SDK does for a client it builds, and leaves to the caller for an
    injected one: re-subscribe, then `republish_tree`. A broker that restarted
    without persistence holds nothing until the tree is announced again."""
    runtime = await _started(_CONFIG)
    assert isinstance(runtime.transport, LoopBoundTransport)
    await runtime.transport.drain()
    first = fake_broker.clients[0]
    filters = [f for f, _ in first.subscribed]

    fake_broker.go_down()
    fake_broker.come_up()
    second = await fake_broker.wait_for_live(after=first)
    await _republished(second)

    assert [f for f, _ in second.subscribed] == filters
    last_subscribe = max(i for i, (kind, _) in enumerate(second.log) if kind == "subscribe")
    first_publish = min(i for i, (kind, _) in enumerate(second.log) if kind == "publish")
    assert last_subscribe < first_publish, "the tree went out before its subscriptions"
    published = {topic for topic, _, _, _ in second.published}
    assert f"{_ROOT}/$description" in published
    assert f"{_ROOT}/$state" in published

    circuit = settable_relays(runtime)[0]
    fake_broker.send(f"ebus/5/{circuit}/switch/relay/set", b"OPEN")
    await relay_opened_by_command(runtime, circuit)
    await emitter_runtime.stop_clone(runtime)


async def _republished(client: FakeClient) -> None:
    async with asyncio.timeout(2):
        while not any(t.endswith("/$state") and p == b"ready" for t, p, _, _ in client.published):
            await asyncio.sleep(0.005)
