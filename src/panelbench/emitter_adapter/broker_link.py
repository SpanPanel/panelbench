"""The panel's connection to its broker, kept up for as long as the panel runs.

An ``aiomqtt.Client`` is one connection. It does not reconnect, and it does not
route: what arrives on it waits in ``client.messages`` until something reads it.
The SDK's own ``MqttClient`` does both for a client it builds — it re-subscribes
every filter on reconnect, calls its on-connect hook, and hands each message to
the callback its filter was subscribed with. For a client injected into the SDK,
as this package's is, all of that is the caller's. This module is the caller's
half.

**One client per connection.** The supervisor enters a fresh client for each
attempt, so nothing survives from a connection the broker no longer knows about:
the session is clean, and the filters the broker forgot are replayed from
``_routes``, which outlives every client.

**A link that keeps dropping backs off.** The delay before a reconnect doubles
up to a ceiling, and resets only once a connection has outlasted that ceiling. A
broker that accepts and then drops the panel at once, as one does when a second
client takes over the same client id, is retried at the ceiling rather than
every second.

**An outage is reported once.** The first sign of it, whether the reader seeing
the link drop or an operation failing on it, logs a single warning. Everything
attempted until the link is back raises ``BrokerUnavailable``, which the transport
drops quietly, and failed reconnect attempts log only at debug.

**Nothing waits on a dead socket.** aiomqtt resolves a QoS 1 publish on its
PUBACK, and a client whose socket has died never gets one, so the publish waits
out aiomqtt's timeout. The transport drains one operation at a time, so for that
long nothing else moves, the republished tree included. Each operation therefore
runs under a deadline the supervisor brings forward to now when the link drops.

**A failed operation ends the session.** A broker can stop answering on a socket
that stays open: a frozen process, a sleeping host, a dropped Wi-Fi link. paho
notices only after up to two keepalive intervals, and until then the reader sees
nothing wrong. So an operation that fails on a live session fails the session:
the session's read deadline is brought forward to now, the supervisor leaves the
client and reconnects, and the reconnect's republished tree restores what the
failure dropped. Leaving the client sends a clean DISCONNECT, which waits up to
the client's operation timeout for a broker that may never answer; the link is
already down by then, so publishes fail fast rather than queue. Only an expired
deadline becomes ``TimeoutError``, so ``disconnect``'s cancellation stays a
cancellation.

**A cancelled connect is finished, then closed.** aiomqtt connects in a thread,
which cancelling the await does not stop. A connect cancelled by ``disconnect``
is therefore left to resolve, and a client it connected is closed, so no orphan
connection holds the panel's client id and will.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiomqtt
from paho.mqtt.client import MQTT_ERR_CONN_LOST, MQTT_ERR_NO_CONN
from paho.mqtt.reasoncodes import ReasonCode

from panelbench.emitter_adapter.transport import BrokerUnavailable, MessageCallback

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

_LOG = logging.getLogger(__name__)

DEFAULT_MIN_RECONNECT_DELAY = 1.0
# A broker restart is usually back within seconds. The ceiling bounds how long a
# panel stays dark after a longer outage ends, at a cost of one refused connection
# per interval while it lasts.
DEFAULT_MAX_RECONNECT_DELAY = 30.0

# The codes that only say the connection is gone. paho reports a broker that closed
# the socket under MQTT 3.1.1, which has no server DISCONNECT, as 128, "Unspecified
# error", which tells an operator nothing.
_UNSPECIFIED_ERROR = 0x80
_CONNECTION_LOSS_CODES = frozenset({int(MQTT_ERR_CONN_LOST), int(MQTT_ERR_NO_CONN)})

# A SUBACK return code at or above this refuses the subscription.
_SUBSCRIPTION_REFUSED = 0x80


class _Backoff:
    """Reconnect delays that double from *first* up to *ceiling*."""

    def __init__(self, *, first: float, ceiling: float) -> None:
        self._first = first
        self.ceiling = ceiling
        self._next = first

    def next_delay(self) -> float:
        delay = self._next
        self._next = min(delay * 2, self.ceiling)
        return delay

    def reset(self) -> None:
        self._next = self._first


@dataclass(frozen=True, slots=True)
class _Route:
    callback: MessageCallback
    qos: int


class _Session:
    """One connected client, the operations in flight on it, and how it ends."""

    def __init__(self, client: aiomqtt.Client) -> None:
        self.client = client
        self.outage_reported = False
        self._in_flight: set[asyncio.Timeout] = set()
        self._serving: asyncio.Timeout | None = None

    @contextlib.asynccontextmanager
    async def serving(self) -> AsyncIterator[None]:
        """The block the supervisor serves this session in: restore, then read.

        ``fail`` ends it, with ``TimeoutError`` out of this block.
        """
        async with asyncio.timeout(None) as deadline:
            self._serving = deadline
            try:
                yield
            finally:
                self._serving = None

    def fail(self) -> None:
        """End this session: an operation on it failed, whatever the reader thinks."""
        now = asyncio.get_running_loop().time()
        if self._serving is not None and not self._serving.expired():
            self._serving.reschedule(now)
        self.abandon()

    @contextlib.asynccontextmanager
    async def operation(self) -> AsyncIterator[aiomqtt.Client]:
        """The client, for one operation that ``abandon`` can cut short.

        Cut short, the operation raises ``TimeoutError`` out of this block.
        """
        async with asyncio.timeout(None) as deadline:
            self._in_flight.add(deadline)
            try:
                yield self.client
            finally:
                self._in_flight.discard(deadline)

    def abandon(self) -> None:
        """End every operation in flight: the link they were waiting on is gone."""
        now = asyncio.get_running_loop().time()
        for deadline in self._in_flight:
            if not deadline.expired():
                deadline.reschedule(now)


class BrokerLink:
    """A supervised aiomqtt connection that reconnects, re-subscribes and routes.

    ``connect`` returns once the first connection is up, and raises if it cannot
    be made, as start-up did before the link supervised itself. From then on the
    supervisor keeps it up until ``disconnect``: after each reconnect it restores
    every subscription and then runs the ``on_reconnect`` hook.

    Single-use: a link that has been opened cannot be opened again, since its
    routes and hook belong to the panel it served.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        will: aiomqtt.Will | None = None,
        ca_cert_path: str | None = None,
        min_reconnect_delay: float = DEFAULT_MIN_RECONNECT_DELAY,
        max_reconnect_delay: float = DEFAULT_MAX_RECONNECT_DELAY,
        operation_timeout: float | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._will = will
        self._ca_cert_path = ca_cert_path
        self._backoff = _Backoff(first=min_reconnect_delay, ceiling=max_reconnect_delay)
        # aiomqtt's own timeout for connecting and for each operation; None is its 10 s.
        self._operation_timeout = operation_timeout
        self._routes: dict[str, _Route] = {}
        self._session: _Session | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._opened = False
        self._on_reconnect: Callable[[], None] | None = None
        # Clients whose connect ``disconnect`` cancelled, held until they are closed.
        self._orphans: set[asyncio.Task[None]] = set()

    # -- lifecycle --------------------------------------------------------------

    async def connect(self) -> None:
        """Open the link, raising ``aiomqtt.MqttError`` if the broker refuses it."""
        if self._opened:
            raise RuntimeError("a broker link opens once; build another for a new panel")
        self._opened = True
        connected: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._supervisor = asyncio.create_task(
            self._supervise(connected), name=f"mqtt-link-{self._client_id}"
        )
        try:
            await connected
        except BaseException:
            await self.disconnect()
            raise

    def on_reconnect(self, hook: Callable[[], None]) -> None:
        """Run *hook* after each reconnect, once every subscription is restored.

        Not after the first connection: whatever is built on the link announces
        itself then. Set after ``connect`` because the hook is typically a method
        of something that needs the link up to be built.
        """
        self._on_reconnect = hook

    async def disconnect(self) -> None:
        """Stop the supervisor and close the client cleanly, which suppresses the will."""
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is None:
            return
        supervisor.cancel()
        await asyncio.wait({supervisor})

    # -- the operations the transport drains into -------------------------------

    def is_connected(self) -> bool:
        return self._session is not None

    async def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> None:
        session = self._live_session()
        try:
            async with session.operation() as client:
                await client.publish(topic, payload=payload, qos=qos, retain=retain)
        except (aiomqtt.MqttError, TimeoutError) as exc:
            self._fail(session, exc)
            raise BrokerUnavailable(f"publish to {topic} was not sent") from exc

    async def subscribe(self, topic_filter: str, callback: MessageCallback, qos: int = 0) -> None:
        """Route messages on *topic_filter* to *callback*, now and after every reconnect.

        Recorded before anything is sent, so a filter the link is down for is
        still restored when it comes back: the SDK subscribes once and never again.
        """
        self._routes[topic_filter] = _Route(callback, qos)
        session = self._live_session()
        try:
            await self._subscribe_on(session, topic_filter, qos)
        except (aiomqtt.MqttError, TimeoutError) as exc:
            self._fail(session, exc)
            raise BrokerUnavailable(f"subscription to {topic_filter} was not sent") from exc

    # -- internals --------------------------------------------------------------

    def _live_session(self) -> _Session:
        session = self._session
        if session is None:
            raise BrokerUnavailable(f"no connection to the MQTT broker at {self._address}")
        return session

    @property
    def _address(self) -> str:
        return f"{self._host}:{self._port}"

    def _new_client(self) -> aiomqtt.Client:
        tls_params = (
            aiomqtt.TLSParameters(ca_certs=self._ca_cert_path) if self._ca_cert_path else None
        )
        return aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            identifier=self._client_id,
            username=self._username,
            password=self._password,
            will=self._will,
            tls_params=tls_params,
            timeout=self._operation_timeout,
        )

    @contextlib.asynccontextmanager
    async def _connected_client(self) -> AsyncIterator[aiomqtt.Client]:
        """A connected client, left with a clean DISCONNECT.

        The connect is shielded: cancelled, it is left to finish in its thread, and a
        client it connected is then closed, so no orphan keeps the panel's id and will.
        """
        client = self._new_client()
        attempt = asyncio.ensure_future(client.__aenter__())
        try:
            await asyncio.shield(attempt)
        except asyncio.CancelledError:
            closing = asyncio.ensure_future(self._close_when_connected(attempt))
            self._orphans.add(closing)
            closing.add_done_callback(self._orphans.discard)
            raise
        try:
            yield client
        finally:
            await client.__aexit__(None, None, None)

    @staticmethod
    async def _close_when_connected(attempt: asyncio.Future[aiomqtt.Client]) -> None:
        try:
            client = await attempt
        except Exception:
            return  # never connected, so nothing to close
        with contextlib.suppress(Exception):
            await client.__aexit__(None, None, None)

    async def _supervise(self, connected: asyncio.Future[None]) -> None:
        loop = asyncio.get_running_loop()
        reconnecting = False
        while True:
            session: _Session | None = None
            opened_at: float | None = None
            try:
                async with self._connected_client() as client:
                    opened_at = loop.time()
                    session = _Session(client)
                    self._session = session
                    try:
                        async with session.serving():
                            if reconnecting:
                                _LOG.info("reconnected to the MQTT broker at %s", self._address)
                                await self._restore(session)
                            else:
                                reconnecting = True
                                if not connected.done():
                                    connected.set_result(None)
                            async for message in client.messages:
                                self._dispatch(message.topic.value, message.payload)
                    finally:
                        self._session = None
                        session.abandon()
            except aiomqtt.MqttError as exc:
                if not reconnecting:
                    self._fail_start(connected, exc)
                    return
                # A refused reconnect has no session: its outage is already reported.
                if session is not None:
                    self._report_outage(session, exc)
            except TimeoutError:
                # The session's deadline: an operation failed it, and reported why.
                pass
            except Exception as exc:
                if not reconnecting:
                    self._fail_start(connected, exc)
                    return
                _LOG.exception("the link to the MQTT broker at %s failed", self._address)
            if opened_at is not None and loop.time() - opened_at >= self._backoff.ceiling:
                self._backoff.reset()
            delay = self._backoff.next_delay()
            _LOG.debug("reconnecting to the MQTT broker at %s in %.2fs", self._address, delay)
            await asyncio.sleep(delay)

    @staticmethod
    def _fail_start(connected: asyncio.Future[None], exc: Exception) -> None:
        """The first connection failed: ``connect`` raises it, the supervisor ends."""
        if not connected.done():
            connected.set_exception(exc)

    async def _restore(self, session: _Session) -> None:
        """Replay every subscription, then run the hook: the order the SDK's client keeps.

        Each subscription is an operation of the session, so a failure anywhere on it
        cuts the restore short and the next reconnect starts it again.
        """
        for topic_filter, route in list(self._routes.items()):
            try:
                await self._subscribe_on(session, topic_filter, route.qos)
            except (aiomqtt.MqttError, TimeoutError) as exc:
                self._fail(session, exc)
                raise
        if self._on_reconnect is None:
            return
        try:
            self._on_reconnect()
        except Exception:
            # The link is up and serving commands; a hook that failed must not end it.
            _LOG.exception("the reconnect hook failed")

    async def _subscribe_on(self, session: _Session, topic_filter: str, qos: int) -> None:
        async with session.operation() as client:
            granted = await client.subscribe(topic_filter, qos=qos)
        if _refused(granted):
            # Parity with the SDK's client would ignore it; a refusal (an ACL, say)
            # otherwise surfaces only as commands that never arrive.
            _LOG.warning(
                "the MQTT broker at %s refused the subscription to %s", self._address, topic_filter
            )

    def _fail(self, session: _Session, exc: BaseException) -> None:
        """An operation on *session* failed: report it once, and end the session.

        Taken down here rather than when the supervisor next runs, so the operations
        queued behind this one fail fast instead of each waiting out a timeout.
        """
        self._report_outage(session, exc)
        if self._session is session:
            self._session = None
        session.fail()

    def _dispatch(self, topic: str, payload: bytes) -> None:
        """Hand a message to the callback of every filter it matches, as a broker would.

        Every one, unlike the SDK's client, which stops at the first match. The SDK
        subscribes exact, non-overlapping ``/set`` topics, so the two agree today.
        """
        delivered = aiomqtt.Topic(topic)
        for topic_filter, route in list(self._routes.items()):
            if not delivered.matches(topic_filter):
                continue
            try:
                route.callback(topic, payload)
            except Exception:
                _LOG.warning("the handler for %s raised", topic, exc_info=True)

    def _report_outage(self, session: _Session, exc: BaseException) -> None:
        if session.outage_reported:
            return
        session.outage_reported = True
        # aiomqtt raises a generic "Disconnected during message iteration" from the
        # error that says what actually happened.
        reason = exc.__cause__ or exc
        if _is_connection_loss(reason):
            _LOG.warning(
                "the connection to the MQTT broker at %s dropped; reconnecting", self._address
            )
            return
        _LOG.warning(
            "lost the MQTT broker at %s (%s); reconnecting",
            self._address,
            str(reason) or type(reason).__name__,
        )


def _code(rc: int | ReasonCode | None) -> int | None:
    return rc.value if isinstance(rc, ReasonCode) else rc


def _is_connection_loss(reason: BaseException) -> bool:
    """Whether *reason* says only that the connection went, and nothing about why.

    A refused CONNACK and an operation timeout keep their own text: those say why.
    """
    if not isinstance(reason, aiomqtt.MqttCodeError):
        return False
    code = _code(reason.rc)
    if isinstance(reason.rc, ReasonCode):
        return code == _UNSPECIFIED_ERROR
    return code in _CONNECTION_LOSS_CODES


def _refused(granted: Sequence[int | ReasonCode] | None) -> bool:
    """Whether a SUBACK refused the subscription it answers."""
    return any(
        (code := _code(g)) is not None and code >= _SUBSCRIPTION_REFUSED for g in granted or ()
    )
