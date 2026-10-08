"""A stand-in for ``aiomqtt.Client``, with a broker behind it that a test can take away.

Installed in place of ``aiomqtt.Client`` (``FakeBroker.install``), so the code under
test builds its clients exactly as it would against a real broker. Each client
records what it was asked to do in one ordered log, because the order is most of
what these tests assert: subscriptions restored before the tree is republished,
nothing published on a client that has been closed.

A severed client behaves the way aiomqtt does when its socket drops: message
iteration raises ``MqttError``, and so does any later publish or subscribe.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import aiomqtt
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class FakeClient:
    """One connection attempt, successful or not, and everything done on it."""

    broker: ClassVar[FakeBroker]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.log: list[tuple[str, str]] = []
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.subscribed: list[tuple[str, int]] = []
        self.entered = False
        self.exited = False
        self.severed = False
        self._lost = asyncio.Event()
        self._inbox: asyncio.Queue[aiomqtt.Message] = asyncio.Queue()
        self.broker.clients.append(self)

    @property
    def connected(self) -> bool:
        return self.entered and not self.exited and not self.severed

    async def __aenter__(self) -> FakeClient:
        if not self.broker.up:
            raise aiomqtt.MqttError("[Errno 61] Connection refused")
        self.entered = True
        if self.broker.drop_on_connect:
            self.sever()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True

    async def publish(
        self, topic: str, payload: bytes | None = None, qos: int = 0, retain: bool = False
    ) -> None:
        if self.exited:
            raise AssertionError(f"published {topic!r} after the client was closed")
        if self.severed:
            raise aiomqtt.MqttError("Could not publish message")
        if self.broker.hang_publishes:
            # A QoS 1 publish whose PUBACK never comes, because the socket died
            # under it. aiomqtt waits out its own timeout; this waits forever.
            await asyncio.Event().wait()
        self.published.append((topic, payload or b"", qos, retain))
        self.log.append(("publish", topic))

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        if self.severed:
            raise aiomqtt.MqttError("Could not subscribe to topic")
        self.subscribed.append((topic, qos))
        self.log.append(("subscribe", topic))

    @property
    def messages(self) -> AsyncIterator[aiomqtt.Message]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[aiomqtt.Message]:
        while True:
            arrival = asyncio.ensure_future(self._inbox.get())
            loss = asyncio.ensure_future(self._lost.wait())
            try:
                done, _ = await asyncio.wait({arrival, loss}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                arrival.cancel()
                loss.cancel()
            if arrival in done:
                yield arrival.result()
            else:
                raise aiomqtt.MqttError("Disconnected during message iteration")

    def sever(self) -> None:
        self.severed = True
        self._lost.set()

    def receive(self, topic: str, payload: bytes) -> None:
        self._inbox.put_nowait(aiomqtt.Message(topic, payload, 1, False, 0, None))


class FakeBroker:
    """Whether the broker is reachable, and every client that tried to reach it."""

    def __init__(self) -> None:
        self.up = True
        self.hang_publishes = False
        # Accept each connection, then drop it at once: what a broker does to a
        # client whose id another client has just taken over.
        self.drop_on_connect = False
        self.clients: list[FakeClient] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FakeClient.broker = self
        monkeypatch.setattr(aiomqtt, "Client", FakeClient)

    @property
    def live(self) -> FakeClient | None:
        connected = [c for c in self.clients if c.connected]
        return connected[-1] if connected else None

    def go_down(self) -> None:
        self.up = False
        for client in self.clients:
            if client.connected:
                client.sever()

    def come_up(self) -> None:
        self.up = True

    def send(self, topic: str, payload: bytes) -> None:
        """Route a message to each connected client subscribed to a matching filter."""
        delivered = aiomqtt.Topic(topic)
        for client in self.clients:
            if client.connected and any(delivered.matches(f) for f, _ in client.subscribed):
                client.receive(topic, payload)

    async def wait_for_live(self, *, after: FakeClient | None = None) -> FakeClient:
        """The next connected client, other than *after*."""
        async with asyncio.timeout(5):
            while (client := self.live) is None or client is after:
                await asyncio.sleep(0.005)
        return client
