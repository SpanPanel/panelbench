"""The broker connection: inbound messages, and a link that outlives its broker.

aiomqtt 2.x neither reconnects nor routes messages: a client is one connection,
and what arrives on it waits in ``client.messages`` until something reads it.
``BrokerLink`` supplies both, and these tests hold it to the contract the SDK's own
``MqttClient`` keeps for a client it builds: every subscription restored on
reconnect, then the on-connect hook, and each message handed to the callback its
filter was subscribed with.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiomqtt
import pytest

from panelbench.emitter_adapter.broker_link import BrokerLink, _Backoff
from panelbench.emitter_adapter.transport import BrokerUnavailable, LoopBoundTransport

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.emitter_adapter._fake_aiomqtt import FakeBroker

RELAY_SETS = "ebus/5/+/switch/relay/set"
PRIORITY_SET = "ebus/5/dev-1/load-shed/priority/set"


class Inbox:
    """A message callback that keeps what it was handed."""

    def __init__(self) -> None:
        self.received: list[tuple[str, bytes]] = []

    def __call__(self, topic: str, payload: bytes) -> None:
        self.received.append((topic, payload))


async def eventually(condition: Callable[[], object], *, within: float = 2.0) -> None:
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0.005)


async def connected_link(*, max_reconnect_delay: float = 0.05) -> BrokerLink:
    link = BrokerLink(
        host="broker.invalid",
        port=1883,
        client_id="span-sim-test",
        min_reconnect_delay=0.01,
        max_reconnect_delay=max_reconnect_delay,
    )
    await link.connect()
    return link


async def outage(broker: FakeBroker, link: BrokerLink) -> None:
    """Take the broker away and wait until the link has noticed."""
    broker.go_down()
    await eventually(lambda: not link.is_connected())


def supervisors() -> list[asyncio.Task[object]]:
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("mqtt-link")]


# -- inbound messages ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_message_reaches_the_callback_its_filter_was_subscribed_with(
    fake_broker: FakeBroker,
) -> None:
    """The defect: HA published `switch/relay/set OPEN` and nothing acted on it."""
    link = await connected_link()
    relays, priority = Inbox(), Inbox()
    await link.subscribe(RELAY_SETS, relays, 1)
    await link.subscribe(PRIORITY_SET, priority, 1)

    fake_broker.send("ebus/5/dev-1/switch/relay/set", b"OPEN")
    fake_broker.send("ebus/5/dev-1/load-shed/priority/set", b"OFF_GRID")
    await eventually(lambda: relays.received and priority.received)

    assert relays.received == [("ebus/5/dev-1/switch/relay/set", b"OPEN")]
    assert priority.received == [("ebus/5/dev-1/load-shed/priority/set", b"OFF_GRID")]
    assert fake_broker.live is not None
    assert fake_broker.live.subscribed == [(RELAY_SETS, 1), (PRIORITY_SET, 1)]
    await link.disconnect()


@pytest.mark.asyncio
async def test_a_raising_callback_does_not_stop_the_next_message(
    fake_broker: FakeBroker,
) -> None:
    """Callbacks run on the link's reader; one that raises must not end it."""
    link = await connected_link()
    inbox = Inbox()

    def explode(topic: str, payload: bytes) -> None:
        raise ValueError("a handler bug")

    await link.subscribe("ebus/5/dev-1/switch/relay/set", explode, 1)
    await link.subscribe("ebus/5/dev-2/switch/relay/set", inbox, 1)

    fake_broker.send("ebus/5/dev-1/switch/relay/set", b"OPEN")
    fake_broker.send("ebus/5/dev-2/switch/relay/set", b"OPEN")
    await eventually(lambda: inbox.received)

    assert inbox.received == [("ebus/5/dev-2/switch/relay/set", b"OPEN")]
    await link.disconnect()


# -- surviving the broker -----------------------------------------------------


@pytest.mark.asyncio
async def test_is_connected_follows_the_live_link(fake_broker: FakeBroker) -> None:
    link = await connected_link()
    assert link.is_connected()

    await outage(fake_broker, link)
    fake_broker.come_up()
    await eventually(link.is_connected)

    await link.disconnect()
    assert not link.is_connected()


@pytest.mark.asyncio
async def test_a_reconnect_restores_every_subscription_before_running_the_hook(
    fake_broker: FakeBroker,
) -> None:
    """The hook is `Emitter.republish_tree`. A tree announced before its `/set`
    filters are back invites a command that nothing is listening for."""
    link = await connected_link()
    restored_at_hook: list[list[tuple[str, int]]] = []
    link.on_reconnect(lambda: restored_at_hook.append(list(fake_broker.clients[-1].subscribed)))
    await link.subscribe(RELAY_SETS, Inbox(), 1)
    await link.subscribe(PRIORITY_SET, Inbox(), 0)
    first = fake_broker.live

    await outage(fake_broker, link)
    assert restored_at_hook == [], "the hook ran without a reconnect"
    fake_broker.come_up()
    await fake_broker.wait_for_live(after=first)
    await eventually(lambda: restored_at_hook)

    assert restored_at_hook == [[(RELAY_SETS, 1), (PRIORITY_SET, 0)]]
    await link.disconnect()


@pytest.mark.asyncio
async def test_a_command_after_a_reconnect_reaches_its_callback(
    fake_broker: FakeBroker,
) -> None:
    link = await connected_link()
    inbox = Inbox()
    await link.subscribe(RELAY_SETS, inbox, 1)

    first = fake_broker.live
    await outage(fake_broker, link)
    fake_broker.come_up()
    await fake_broker.wait_for_live(after=first)
    await eventually(lambda: fake_broker.live is not None and fake_broker.live.subscribed)
    fake_broker.send("ebus/5/dev-1/switch/relay/set", b"OPEN")
    await eventually(lambda: inbox.received)

    assert inbox.received == [("ebus/5/dev-1/switch/relay/set", b"OPEN")]
    await link.disconnect()


@pytest.mark.asyncio
async def test_a_subscription_made_during_an_outage_is_restored(
    fake_broker: FakeBroker,
) -> None:
    """The SDK subscribes once and never again, so a filter the outage swallowed
    has to come back with the link or it is gone for good."""
    link = await connected_link()
    inbox = Inbox()
    first = fake_broker.live

    await outage(fake_broker, link)
    with pytest.raises(BrokerUnavailable):
        await link.subscribe(RELAY_SETS, inbox, 1)
    fake_broker.come_up()
    await fake_broker.wait_for_live(after=first)
    await eventually(lambda: fake_broker.live is not None and fake_broker.live.subscribed)
    fake_broker.send("ebus/5/dev-1/switch/relay/set", b"OPEN")
    await eventually(lambda: inbox.received)

    assert inbox.received == [("ebus/5/dev-1/switch/relay/set", b"OPEN")]
    await link.disconnect()


@pytest.mark.asyncio
async def test_publishing_without_a_broker_says_so(fake_broker: FakeBroker) -> None:
    link = await connected_link()
    await outage(fake_broker, link)

    with pytest.raises(BrokerUnavailable):
        await link.publish("ebus/5/dev-1/$state", b"ready", 1, True)

    await link.disconnect()


@pytest.mark.asyncio
async def test_a_publish_in_flight_when_the_link_drops_is_abandoned(
    fake_broker: FakeBroker,
) -> None:
    """A QoS 1 publish whose socket dies waits for a PUBACK that never comes —
    aiomqtt's timeout, ten seconds by default. The drainer is single-file, so for
    those seconds nothing else moves, the republished tree included."""
    link = await connected_link()
    fake_broker.hang_publishes = True
    in_flight = asyncio.create_task(link.publish("ebus/5/dev-1/$state", b"ready", 1, True))
    await asyncio.sleep(0.01)

    fake_broker.go_down()

    with pytest.raises(BrokerUnavailable):
        await asyncio.wait_for(in_flight, timeout=1.0)
    await link.disconnect()


@pytest.mark.asyncio
async def test_an_outage_is_reported_once_and_without_a_traceback(
    fake_broker: FakeBroker, caplog: pytest.LogCaptureFixture
) -> None:
    """Every tick publishes hundreds of topics. Logging each casualty, with a
    traceback, is what buried the one line that said the broker had gone."""
    caplog.set_level(logging.DEBUG)
    link = await connected_link()
    transport = LoopBoundTransport(
        publish=link.publish, subscribe=link.subscribe, connected=link.is_connected
    )
    transport.start()

    def warnings() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.levelno >= logging.WARNING]

    for outage_number in (1, 2):
        live = fake_broker.live
        fake_broker.go_down()
        # Submitted the instant the socket dies, so the drainer can meet the dead
        # client before the reader does: either may notice first, and only one
        # of them may say so.
        for i in range(50):
            transport.publish(f"ebus/5/dev-1/node/p{i}", str(i))
        await transport.drain()
        await eventually(lambda: not link.is_connected())
        await asyncio.sleep(0.1)  # several refused reconnects

        assert len(warnings()) == outage_number, [r.getMessage() for r in warnings()]
        assert all(r.exc_info is None for r in warnings())

        fake_broker.come_up()
        await fake_broker.wait_for_live(after=live)

    await transport.aclose()
    await link.disconnect()


# -- lifecycle ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_closes_the_client_and_stops_the_supervisor(
    fake_broker: FakeBroker,
) -> None:
    link = await connected_link()
    client = fake_broker.live
    assert client is not None and supervisors()

    await link.disconnect()

    assert client.exited
    assert not supervisors()


@pytest.mark.asyncio
async def test_disconnect_during_an_outage_stops_the_retries(fake_broker: FakeBroker) -> None:
    link = await connected_link()
    await outage(fake_broker, link)

    await link.disconnect()
    attempts = len(fake_broker.clients)
    fake_broker.come_up()
    await asyncio.sleep(0.1)

    assert not supervisors()
    assert len(fake_broker.clients) == attempts, "the link kept reconnecting after disconnect"


@pytest.mark.asyncio
async def test_a_broker_down_at_start_fails_the_connect(fake_broker: FakeBroker) -> None:
    """Start-up still fails loudly, as it did before the link supervised itself:
    a panel that cannot reach its broker at all is a configuration error."""
    fake_broker.up = False
    link = BrokerLink(host="broker.invalid", port=1883, client_id="span-sim-test")

    with pytest.raises(aiomqtt.MqttError):
        async with asyncio.timeout(2):  # retrying the first connection would hang here
            await link.connect()

    assert not supervisors()
    assert not link.is_connected()


@pytest.mark.asyncio
async def test_a_link_that_drops_as_soon_as_it_connects_backs_off(
    fake_broker: FakeBroker,
) -> None:
    """Resetting the delay on every connection would retry a broker that takes
    the panel's client id away each time at the floor, forever, with a warning
    for each. The delay resets only once a connection has outlasted the ceiling."""
    link = await connected_link()  # delays double from 0.01s to 0.05s
    fake_broker.drop_on_connect = True
    fake_broker.go_down()
    fake_broker.come_up()
    attempts = len(fake_broker.clients)

    await asyncio.sleep(0.5)

    # At the ceiling, 0.5s holds about ten attempts; at the floor, about fifty.
    assert len(fake_broker.clients) - attempts < 20
    await link.disconnect()


@pytest.mark.asyncio
async def test_a_link_that_held_reconnects_promptly_after_its_next_drop(
    fake_broker: FakeBroker,
) -> None:
    """The other half: a connection that outlasted the ceiling was a real one, so
    the next outage starts again from the floor, not from where the last left off."""
    link = await connected_link(max_reconnect_delay=1.0)
    await outage(fake_broker, link)
    await asyncio.sleep(0.7)  # refused at 0.01s, 0.02s, ... 0.32s: next wait 0.64s
    fake_broker.come_up()
    await eventually(link.is_connected, within=2.0)
    await asyncio.sleep(1.1)  # outlasts the ceiling

    await outage(fake_broker, link)
    fake_broker.come_up()

    await eventually(link.is_connected, within=0.5)  # the ceiling would be 1.0s
    await link.disconnect()


def test_the_reconnect_delay_doubles_to_its_ceiling_and_resets() -> None:
    backoff = _Backoff(first=1.0, ceiling=30.0)

    delays = [backoff.next_delay() for _ in range(7)]
    backoff.reset()

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert backoff.next_delay() == 1.0
