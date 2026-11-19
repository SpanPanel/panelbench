"""Shared test fixtures for the simulator test suite.

Post-emitter cutover: the legacy snapshot/publisher fixtures have been removed because
the underlying types (SpanPanelSnapshot, HomiePublisher) no longer exist on the
simulator side — they live in the emitter package as Ebus*Snapshot."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from amqtt.broker import Broker

from tests._helpers import lay_every_template

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


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


@pytest.fixture(scope="session")
def template_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A config directory holding `EVERY_TEMPLATE`, laid once for the session.

    Shared by every test that takes it, so a test reads from it and never writes to it;
    one that needs a directory of its own lays the templates into its ``tmp_path``.
    """
    config_dir = tmp_path_factory.mktemp("templates")
    lay_every_template(config_dir)
    return config_dir
