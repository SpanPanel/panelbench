"""Discovering a panel's tree: a broker that cannot be reached, and a root that
never described itself, each say so rather than reading as a panel with no circuits.

The live failure this pins: a scrape dialled the broker name the panel advertises,
a ``.local`` name that does not resolve across subnets, never connected, and was
reported as a panel that had published "none of its circuits".
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest
from ebus_sdk import DiscoveredDevice

from panelbench.scraper import (
    BrokerRefused,
    ScrapeError,
    _discover_tree,
    _validate_discovered_tree,
)


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


async def _discover(port: int, *, connect_timeout: float) -> dict[str, DiscoveredDevice]:
    return await _discover_tree(
        host="127.0.0.1",
        port=port,
        root="example-panel-001",
        username=None,
        password=None,
        ca_cert_path=None,
        connect_timeout=connect_timeout,
        stability_timeout=0.2,
        max_timeout=2.0,
        status_callback=None,
    )


@pytest.mark.asyncio
async def test_a_refused_connection_is_a_connecting_error_naming_the_broker() -> None:
    port = _closed_port()

    with pytest.raises(ScrapeError) as raised:
        await _discover(port, connect_timeout=5.0)

    assert raised.value.phase == "connecting"
    assert f"127.0.0.1:{port}" in str(raised.value)
    assert not isinstance(raised.value, BrokerRefused), "nothing refused the credentials"


@pytest.fixture
async def silent_broker() -> AsyncIterator[int]:
    """A port that accepts a connection and never answers it, as a black-holed or
    hung broker does."""
    held: list[asyncio.StreamWriter] = []

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_a_broker_that_never_answers_is_a_connecting_error_within_its_timeout(
    silent_broker: int,
) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(ScrapeError) as raised:
        await _discover(silent_broker, connect_timeout=0.5)

    assert raised.value.phase == "connecting"
    assert f"127.0.0.1:{silent_broker}" in str(raised.value)
    assert loop.time() - started < 3.0


async def _answering_every_connect_with(return_code: int) -> asyncio.Server:
    """A broker that answers each CONNECT with an MQTT 3.1.1 CONNACK of *return_code*."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(1024)  # the CONNECT
        writer.write(bytes((0x20, 0x02, 0x00, return_code)))
        await writer.drain()
        await reader.read()  # until the client goes
        writer.close()

    return await asyncio.start_server(answer, "127.0.0.1", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("return_code", "refused"),
    [
        # Unavailable, or a rejected id: a broker restarting, or one that cannot serve
        # this client, says nothing about the credentials, and registering again
        # would leave another client on the panel for nothing. paho reports 3 as
        # 0x88, "Server unavailable". (Not 1: paho answers that by retrying as 3.1.)
        (2, False),
        (3, False),
        # Bad credentials, or not authorised: the only answers that refuse them.
        (4, True),
        (5, True),
    ],
)
async def test_only_a_connack_that_refuses_the_credentials_is_a_refusal(
    return_code: int, refused: bool
) -> None:
    server = await _answering_every_connect_with(return_code)
    port: int = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(ScrapeError) as raised:
            await _discover(port, connect_timeout=5.0)
    finally:
        server.close()
        await server.wait_closed()

    assert raised.value.phase == "connecting"
    assert isinstance(raised.value, BrokerRefused) is refused


def test_a_root_that_never_described_itself_is_reported_as_that() -> None:
    """The SDK creates the root's entry before anything arrives, so a root that is
    present is not a root that described itself."""
    serial = "example-panel-001"

    with pytest.raises(ScrapeError, match=r"\$description"):
        _validate_discovered_tree({serial: DiscoveredDevice(serial)}, serial)
