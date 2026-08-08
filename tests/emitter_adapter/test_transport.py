"""The synchronous seam between the SDK's publish path and aiomqtt."""

from __future__ import annotations

import asyncio
import pathlib

import pytest
import yaml
from ebus_panel_sim import Emitter, MqttDeviceTransport, SetterRegistry

from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.transport import (
    LoopBoundTransport,
    TransportBacklogFull,
)

_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "configs" / "default_MAIN_40.yaml"


class Recorder:
    """Stands in for the aiomqtt client, recording the order it was called in."""

    def __init__(self, delay_first: float = 0.0) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.subscribed: list[str] = []
        self.failures = 0
        self._delay_first = delay_first

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        # A slow first publish is what makes an ordering bug observable: a
        # task-per-publish design lets later, faster publishes overtake it.
        if self._delay_first and not self.published:
            await asyncio.sleep(self._delay_first)
        self.published.append((topic, payload, qos, retain))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    def is_connected(self) -> bool:
        return True


def _transport(recorder: Recorder, **kwargs: object) -> LoopBoundTransport:
    return LoopBoundTransport(
        publish=recorder.publish,
        subscribe=recorder.subscribe,
        connected=recorder.is_connected,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_it_satisfies_the_protocol_the_sdk_type_checks_against() -> None:
    """The claim the whole adapter rests on, asserted against the real protocol.

    ``MqttDeviceTransport`` has a data member, so this must be ``isinstance`` and
    not ``issubclass`` — and ``is_running`` therefore has to exist on the instance
    before the first publish, not be set later by ``start()``.
    """
    transport = _transport(Recorder())

    assert isinstance(transport, MqttDeviceTransport)


@pytest.mark.asyncio
async def test_a_real_emitter_starts_and_stops_without_touching_our_client() -> None:
    """The guarantee that makes injection safe, exercised through the real Emitter.

    ``MqttDeviceTransport`` omits ``start`` / ``stop`` because the SDK resolves
    those only on a client it built. This class implements neither, so if the SDK
    ever reached for one the call would raise ``AttributeError`` here rather than
    quietly tearing down a connection this package owns and is still using.

    Asserted through ``Emitter`` rather than against the ownership helper, which
    lives behind a private seam: what matters is the behaviour at the boundary,
    not the mechanism the far side happens to use today.
    """
    recorder = Recorder()
    transport = _transport(recorder)
    transport.start()
    manifest = build_manifest(yaml.safe_load(_CONFIG.read_text()))

    emitter = Emitter(manifest, SetterRegistry(), mqttc=transport)
    emitter.start()
    emitter.stop(graceful=True)
    await transport.drain()

    assert recorder.published, "the emitter published nothing through the transport"
    assert transport.is_running, "the SDK stopped a client it does not own"


@pytest.mark.asyncio
async def test_publishes_reach_the_client_in_submission_order() -> None:
    """Homie needs `$description` before `$state=ready`; a consumer that sees
    ready first reads a description that does not exist yet.

    The first publish is deliberately slow. Under a task-per-publish design the
    later ones overtake it and this fails; a single drained queue cannot.
    """
    recorder = Recorder(delay_first=0.05)
    transport = _transport(recorder)
    transport.start()

    for i in range(10):
        transport.publish(f"ebus/5/dev/node/p{i}", str(i))
    await transport.drain()

    assert [t for t, _, _, _ in recorder.published] == [f"ebus/5/dev/node/p{i}" for i in range(10)]


@pytest.mark.asyncio
async def test_publish_returns_without_waiting_for_the_broker() -> None:
    """The property that makes a sync transport viable at all.

    The SDK calls this from synchronous code on the event loop. If it blocked,
    it would block the loop that is supposed to be draining it — a deadlock, not
    a slowdown.
    """
    recorder = Recorder(delay_first=0.2)
    transport = _transport(recorder)
    transport.start()

    loop = asyncio.get_running_loop()
    before = loop.time()
    transport.publish("ebus/5/dev/node/p", "1")
    elapsed = loop.time() - before

    assert elapsed < 0.01, "publish waited on the client"
    assert recorder.published == [], "publish reached the client synchronously"
    await transport.drain()
    assert len(recorder.published) == 1


@pytest.mark.asyncio
async def test_a_backlog_fails_loudly_rather_than_growing_without_bound() -> None:
    """Replaces the implicit pacing that `await publish` used to provide."""
    transport = _transport(Recorder(), max_pending=4)

    for i in range(4):
        transport.publish(f"t{i}", "x")

    with pytest.raises(TransportBacklogFull, match="outrunning the broker"):
        transport.publish("one-too-many", "x")


@pytest.mark.asyncio
async def test_a_failing_publish_does_not_mute_the_ones_behind_it() -> None:
    """The SDK has already recorded the value as sent and will not resend it, so
    a drainer that dies on one failure silently mutes every later topic."""
    recorder = Recorder()

    async def explode_once(topic: str, payload: bytes, qos: int, retain: bool) -> None:
        if topic == "boom":
            raise ConnectionResetError("broker went away")
        await recorder.publish(topic, payload, qos, retain)

    transport = LoopBoundTransport(
        publish=explode_once,
        subscribe=recorder.subscribe,
        connected=recorder.is_connected,
    )
    transport.start()

    transport.publish("boom", "x")
    transport.publish("after", "y")
    await transport.drain()

    assert [t for t, _, _, _ in recorder.published] == ["after"]


@pytest.mark.asyncio
async def test_aclose_flushes_what_is_queued_before_stopping() -> None:
    """Teardown publishes the state that tells consumers what happened. Dropping
    it is the one loss the retained tree cannot recover from."""
    recorder = Recorder(delay_first=0.05)
    transport = _transport(recorder)
    transport.start()

    transport.publish("ebus/5/dev/$state", "lost")
    await transport.aclose()

    assert [t for t, _, _, _ in recorder.published] == ["ebus/5/dev/$state"]
    assert transport.is_running is False


@pytest.mark.asyncio
async def test_subscriptions_go_through_the_same_queue_as_publishes() -> None:
    """Held in one queue so a subscription cannot land before the publish that
    precedes it, which is how a `/set` route can miss its own initial value."""
    recorder = Recorder()
    transport = _transport(recorder)
    transport.start()

    transport.publish("ebus/5/dev/node/p", "1")
    transport.subscribe("ebus/5/dev/node/p/set")
    await transport.drain()

    assert recorder.published and recorder.subscribed == ["ebus/5/dev/node/p/set"]
