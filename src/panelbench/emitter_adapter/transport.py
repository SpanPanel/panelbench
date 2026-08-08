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
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_LOG = logging.getLogger(__name__)

# One tick of a 40-space panel publishes on the order of 500 topics, and the
# cold-start tree is larger still. This holds several ticks' worth so a transient
# broker stall is absorbed, while staying small enough that a producer looping
# without a broker fails in seconds rather than exhausting memory.
DEFAULT_MAX_PENDING = 4096


class TransportBacklogFull(RuntimeError):
    """The publish queue hit its bound: the producer is outrunning the broker."""


class LoopBoundTransport:
    """Satisfies ``ebus_sdk.MqttDeviceTransport`` on top of an async MQTT client.

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
        subscribe: Callable[[str], Awaitable[None]],
        connected: Callable[[], bool],
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        self._publish = publish
        self._subscribe = subscribe
        self._connected = connected
        self._queue: asyncio.Queue[tuple[str, bytes, int, bool] | str] = asyncio.Queue(
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
        self._submit((topic, str(data).encode(), qos, retain))
        return None

    def subscribe(self, sub: str, param: Any = None, qos: int = 1) -> object:
        del param, qos  # routing is the SDK's; this transport only carries the filter
        self._submit(sub)
        return None

    # -- internals --------------------------------------------------------------

    def _submit(self, item: tuple[str, bytes, int, bool] | str) -> None:
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
                if isinstance(item, str):
                    await self._subscribe(item)
                else:
                    topic, payload, qos, retain = item
                    await self._publish(topic, payload, qos, retain)
            except Exception:
                # One failed publish must not kill the drainer: the SDK has already
                # been told the value went out and will not resend it, so a dead
                # drainer would silently mute every later topic too.
                _LOG.exception("dropping an MQTT operation that failed in transit")
            finally:
                self._queue.task_done()
