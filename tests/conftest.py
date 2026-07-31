"""Shared test fixtures for the simulator test suite.

Post-emitter cutover: the legacy snapshot/publisher fixtures have been removed because
the underlying types (SpanPanelSnapshot, HomiePublisher) no longer exist on the
simulator side — they live in the emitter package as Ebus*Snapshot."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

import pytest_asyncio
from amqtt.broker import Broker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest_asyncio.fixture
async def amqtt_broker() -> AsyncIterator[tuple[str, int]]:
    """In-process pure-Python MQTT broker (amqtt) for tests that need a live broker.
    Yields (host, port). No system mosquitto required."""
    port = _free_port()
    # Use the v0.11+ "plugins" config form instead of EntryPoint-based discovery
    # (which amqtt deprecated). Declare the anonymous-auth plugin explicitly so
    # tests can connect without credentials; declaring `plugins` here means
    # `auth` / `topic-check` sections of config are ignored.
    broker = Broker(
        config={
            "listeners": {
                "default": {"type": "tcp", "bind": f"127.0.0.1:{port}"},
            },
            "plugins": {
                "amqtt.plugins.authentication.AnonymousAuthPlugin": {
                    "allow_anonymous": True,
                },
            },
        },
    )
    await broker.start()
    await asyncio.sleep(0.05)
    try:
        yield ("127.0.0.1", port)
    finally:
        await broker.shutdown()
