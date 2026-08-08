"""The production publishing path: aiomqtt behind a synchronous SDK transport.

``wire_capture`` injects a ``RecordingTransport``, which is synchronous and has
no queue, so every fidelity capture bypasses ``LoopBoundTransport`` entirely.
Nothing else exercised the branch of ``start_clone`` that builds one — the branch
a real panel always takes. These tests stand a fake broker client behind it so
the queue, the drain and the teardown ordering run for real.
"""

from __future__ import annotations

import pathlib
from typing import Any, ClassVar

import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.transport import LoopBoundTransport
from panelbench.engine import DynamicSimulationEngine

_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "configs" / "default_MAIN_40.yaml"


class FakeBrokerClient:
    """Enough of ``aiomqtt.Client`` for the path under test, recording order."""

    instances: ClassVar[list[FakeBrokerClient]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.entered = False
        self.exited = False
        FakeBrokerClient.instances.append(self)

    async def __aenter__(self) -> FakeBrokerClient:
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True

    async def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> None:
        if self.exited:
            raise AssertionError(f"published {topic!r} after the client was closed")
        self.published.append((topic, payload, qos, retain))

    async def subscribe(self, topic: str) -> None:
        return None


@pytest.fixture
def fake_broker(monkeypatch: pytest.MonkeyPatch) -> type[FakeBrokerClient]:
    FakeBrokerClient.instances = []
    monkeypatch.setattr(emitter_runtime.aiomqtt, "Client", FakeBrokerClient)
    return FakeBrokerClient


async def _started(config: pathlib.Path) -> emitter_runtime.CloneRuntime:
    engine = DynamicSimulationEngine(config_path=config)
    await engine.initialize_async()
    return await emitter_runtime.start_clone(engine)


@pytest.mark.asyncio
async def test_the_tree_reaches_the_broker_through_the_queue(
    fake_broker: type[FakeBrokerClient],
) -> None:
    """The whole point: a synchronous SDK publish path feeding an async client."""
    runtime = await _started(_CONFIG)
    assert isinstance(runtime.transport, LoopBoundTransport)
    await emitter_runtime.publish_tick(runtime)
    await runtime.transport.drain()

    client = fake_broker.instances[0]
    topics = [t for t, _, _, _ in client.published]

    assert client.entered
    assert len(topics) > 100, f"implausibly few topics for a 40-space panel: {len(topics)}"
    assert any(t.endswith("/$description") for t in topics)
    assert all(t.startswith("ebus/5/") for t in topics)

    await emitter_runtime.stop_clone(runtime)


@pytest.mark.asyncio
async def test_each_device_describes_itself_before_it_announces_ready(
    fake_broker: type[FakeBrokerClient],
) -> None:
    """The ordering guarantee, asserted where it actually matters.

    A consumer that sees `$state=ready` first reads a `$description` that has not
    arrived. The transport's own test proves the queue preserves submission
    order; this proves the order submitted is the one Homie requires.
    """
    runtime = await _started(_CONFIG)
    await runtime.transport.drain()  # type: ignore[union-attr]

    client = fake_broker.instances[0]
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
    fake_broker: type[FakeBrokerClient],
) -> None:
    """`stop` queues the root's final state and cannot flush it — the SDK's
    publish path is synchronous and the client behind it is not. Closing first
    would drop exactly the message that tells consumers the producer went away,
    and the fake raises rather than letting that pass silently.
    """
    runtime = await _started(_CONFIG)
    await emitter_runtime.publish_tick(runtime)

    await emitter_runtime.stop_clone(runtime, graceful=True)

    client = fake_broker.instances[0]
    assert client.exited, "the client was never closed"
    root_states = [
        payload
        for topic, payload, _q, _r in client.published
        if topic == "ebus/5/sim-40t-001/$state"
    ]
    assert root_states, "the root never published a state"
    assert root_states[-1] != b"ready", (
        f"the retained root state after teardown is {root_states[-1]!r}"
    )
