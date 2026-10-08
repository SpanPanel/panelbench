"""A synchronous transport the SDK can publish through, backed by an async client.

The eBus SDK's device publish path is synchronous, and this package's MQTT client
is ``aiomqtt``. That looks like an impasse, and it is why the emitter was vendored
and rewritten async in the first place. It is not one.

``MqttTransport`` says so directly: *"Returns are ``object`` because every call
site in the SDK discards them."* The SDK never awaits a publish and never inspects
its result. So a ``publish`` that hands the work to an async client and returns
immediately is a complete implementation of the contract, not a shortcut around
it — and that is what lets this package keep aiomqtt, with the broker, TLS and
add-on wiring already built around it, while the SDK takes back ownership of
topic construction, payload encoding and ``$state``.

**Ordering is the reason this is a queue rather than a task per publish.** Homie
requires a device's ``$description`` to precede its ``$state=ready``: a consumer
that sees ready first will read a description that does not yet exist. Firing an
independent task per publish leaves the interleaving to the scheduler and to
aiomqtt's internals, so the guarantee would hold by luck. One queue drained by one
task preserves submission order by construction.

**Backpressure is now explicit.** Publishing through ``await client.publish(...)``
paced each tick against the broker implicitly. Nothing about the SDK's contract
preserves that, so the choice surfaces here as a bounded queue: a producer that
outruns its broker fails loudly at a stated depth instead of growing the heap
until the add-on is killed.

**A subscription is a filter and a callback.** The SDK subscribes each settable
property's ``/set`` topic with the callback that applies a command to it, and
expects whatever carries the filter to call that callback with the topic and
payload of each message on it, as ``ebus_mqtt_client.MqttClient`` does. Both go
through the queue together, so the client behind it can route what arrives.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable

_LOG = logging.getLogger(__name__)

MessageCallback = Callable[[str, bytes], object]
"""What the SDK subscribes with: called with a message's topic and payload.

The contract ``ebus_mqtt_client.MqttClient`` keeps and ``Property._settable_callback``
is written against. The return is discarded, which is why it is ``object``.
"""

# One tick of a 40-space panel publishes on the order of 500 topics, and the
# cold-start tree is larger still. This holds several ticks' worth so a transient
# broker stall is absorbed, while staying small enough that a producer looping
# without a broker fails in seconds rather than exhausting memory.
DEFAULT_MAX_PENDING = 4096


class TransportBacklogFull(RuntimeError):
    """The publish queue hit its bound: the producer is outrunning the broker."""


class BrokerUnavailable(ConnectionError):
    """The broker link is down, so the operation was not sent.

    Raised by the client behind this transport rather than by the transport, and
    dropped quietly when it reaches the drainer: the client reports an outage once,
    and what an outage drops is restored when the link comes back, since the
    subscriptions are replayed and the tree republished.
    """


@dataclass(frozen=True, slots=True)
class _Publish:
    topic: str
    payload: bytes
    qos: int
    retain: bool


@dataclass(frozen=True, slots=True)
class _Subscribe:
    topic_filter: str
    callback: MessageCallback
    qos: int


@dataclass(frozen=True, slots=True)
class _Unsubscribe:
    topic_filter: str


class LoopBoundTransport:
    """Satisfies ``ebus_sdk.MqttDeviceTransport`` on top of an async MQTT client.

    And ``MqttControllerTransport`` too, which adds ``unsubscribe``: the scraper
    discovers a panel's tree through a ``Controller`` on the same client and
    transport a simulated panel publishes through.

    Deliberately not an ``MqttClient``. ``owned_client()`` narrows by
    ``isinstance(mqttc, MqttClient)``, so this is correctly classified as
    caller-owned: the SDK will never start or stop it, and its lifecycle stays
    with the code that built it. That is the whole point of injecting one.
    """

    # A data member of the protocol, not a method — the SDK's device publish path
    # reads it to gate publishing, so it must exist before the first publish.
    is_running: bool

    def __init__(
        self,
        *,
        publish: Callable[[str, bytes, int, bool], Awaitable[None]],
        subscribe: Callable[[str, MessageCallback, int], Awaitable[None]],
        unsubscribe: Callable[[str], Awaitable[None]],
        connected: Callable[[], bool],
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        self._publish = publish
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._connected = connected
        self._queue: asyncio.Queue[_Publish | _Subscribe | _Unsubscribe] = asyncio.Queue(
            maxsize=max_pending
        )
        self._drainer: asyncio.Task[None] | None = None
        self.is_running = False

    # -- lifecycle, owned by this package rather than by the SDK ----------------

    def start(self) -> None:
        """Begin draining. Named for this package's own callers; the SDK never
        calls it, because ``MqttDeviceTransport`` omits ``start`` precisely so an
        injected client's lifecycle cannot be touched."""
        if self._drainer is None:
            self._drainer = asyncio.get_running_loop().create_task(self._drain())
        self.is_running = True

    async def aclose(self) -> None:
        """Flush what is queued, then stop draining.

        Waits on the queue rather than cancelling it: the last thing a teardown
        publishes is the state that tells consumers what happened, and dropping it
        is the one loss that cannot be recovered from the retained tree.
        """
        self.is_running = False
        if self._drainer is None:
            return
        await self._queue.join()
        self._drainer.cancel()
        self._drainer = None

    async def drain(self) -> None:
        """Wait until everything submitted so far has reached the client."""
        await self._queue.join()

    # -- the MqttDeviceTransport surface ---------------------------------------

    def is_connected(self) -> bool:
        return self._connected()

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> object:
        self._submit(_Publish(topic, str(data).encode(), qos, retain))
        return None

    def subscribe(self, sub: str, param: MessageCallback, qos: int = 1) -> object:
        """``param`` is the SDK's name for the callback, kept so it can pass it by name."""
        self._submit(_Subscribe(sub, param, qos))
        return None

    def unsubscribe(self, sub: str) -> object:
        self._submit(_Unsubscribe(sub))
        return None

    # -- internals --------------------------------------------------------------

    def _submit(self, item: _Publish | _Subscribe | _Unsubscribe) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise TransportBacklogFull(
                f"MQTT publish backlog reached {self._queue.maxsize} pending items; "
                "the producer is outrunning the broker"
            ) from exc

    async def _drain(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _Subscribe):
                    await self._subscribe(item.topic_filter, item.callback, item.qos)
                elif isinstance(item, _Unsubscribe):
                    await self._unsubscribe(item.topic_filter)
                else:
                    await self._publish(item.topic, item.payload, item.qos, item.retain)
            except BrokerUnavailable:
                # Already reported, once, by the link. Logging every casualty would
                # bury that line under one per topic per tick.
                _LOG.debug("dropping an MQTT operation: the broker link is down")
            except Exception:
                # One failed publish must not kill the drainer: the SDK has already
                # been told the value went out and will not resend it, so a dead
                # drainer would silently mute every later topic too.
                _LOG.exception("dropping an MQTT operation that failed in transit")
            finally:
                self._queue.task_done()
