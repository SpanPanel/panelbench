"""A running panel outlives a restart of its broker: a real mosquitto, restarted under it.

The fake client in ``tests/emitter_adapter`` pins the link's contract. This pins
the parts a fake cannot speak for: that aiomqtt notices a broker that went away,
that a broker restarted without persistence comes back holding nothing, and that
a command Home Assistant publishes after the restart reaches the panel.

Skipped where mosquitto is not installed. The in-process amqtt broker the other
tests use cannot stand in here: a restart has to take the process away, sockets
and retained store with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import aiomqtt
import pytest

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.runtime import BrokerConnection
from panelbench.emitter_adapter.transport import LoopBoundTransport
from panelbench.engine import DynamicSimulationEngine
from tests._helpers import DEFAULT_CONFIG, relay_opened_by_command, settable_relays

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

# Package managers put mosquitto in an sbin directory, which is often not on PATH.
_MOSQUITTO = shutil.which(
    "mosquitto",
    path=os.pathsep.join(
        [os.environ.get("PATH", ""), "/opt/homebrew/sbin", "/usr/local/sbin", "/usr/sbin"]
    ),
)

pytestmark = pytest.mark.skipif(_MOSQUITTO is None, reason="mosquitto is not installed")


class Mosquitto:
    """A mosquitto process on a fixed port that can be stopped and started again.

    No persistence, so a restart empties the retained store the way a broker
    that lost it would: only a republished tree can refill it.
    """

    def __init__(self, executable: str, workdir: Path) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port: int = probe.getsockname()[1]
        self._executable = executable
        self._config = workdir / "mosquitto.conf"
        self._config.write_text(
            f"listener {self.port} 127.0.0.1\nallow_anonymous true\npersistence false\n",
            encoding="utf-8",
        )
        self._log = workdir / "mosquitto.log"
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        with self._log.open("ab") as log:
            self._process = await asyncio.create_subprocess_exec(
                self._executable, "-c", str(self._config), stdout=log, stderr=subprocess.STDOUT
            )
        async with asyncio.timeout(5):
            while True:
                with contextlib.suppress(OSError):
                    _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                    writer.close()
                    await writer.wait_closed()
                    return
                await asyncio.sleep(0.02)

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        await process.wait()


@pytest.fixture
async def mosquitto(tmp_path: Path) -> AsyncIterator[Mosquitto]:
    assert _MOSQUITTO is not None
    broker = Mosquitto(_MOSQUITTO, tmp_path)
    await broker.start()
    try:
        yield broker
    finally:
        await broker.stop()


async def eventually(condition: Callable[[], bool], *, within: float) -> None:
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0.02)


async def command(port: int, circuit: str, value: str) -> None:
    """Publish a relay command the way Home Assistant does."""
    async with aiomqtt.Client("127.0.0.1", port, identifier="home-assistant") as ha:
        await ha.publish(f"ebus/5/{circuit}/switch/relay/set", value.encode(), qos=1)


async def retained(port: int, topic: str) -> bytes:
    """What a consumer connecting now is handed for *topic*."""
    async with aiomqtt.Client("127.0.0.1", port, identifier="observer") as observer:
        await observer.subscribe(topic)
        async with asyncio.timeout(5):
            async for message in observer.messages:
                return message.payload
    raise AssertionError(f"nothing retained on {topic}")


@pytest.mark.asyncio
async def test_a_panel_outlives_a_restart_of_its_broker(
    mosquitto: Mosquitto, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    engine = DynamicSimulationEngine(config_path=DEFAULT_CONFIG)
    await engine.initialize_async()
    runtime = await emitter_runtime.start_clone(
        engine, broker=BrokerConnection(host="127.0.0.1", port=mosquitto.port)
    )
    root = f"ebus/5/{engine.serial_number}"
    transport = runtime.transport
    assert isinstance(transport, LoopBoundTransport)
    try:
        await emitter_runtime.publish_tick(runtime)
        # A command is not retained: one sent before the subscription is up is lost.
        await transport.drain()
        before_restart, after_restart = settable_relays(runtime)[:2]

        await command(mosquitto.port, before_restart, "OPEN")
        await relay_opened_by_command(runtime, before_restart)

        await mosquitto.stop()
        await eventually(lambda: not transport.is_connected(), within=5)
        for _ in range(3):
            # The tick loop does not stop for an outage; what it publishes meanwhile
            # must be dropped without a word, since the link has already said it.
            await emitter_runtime.publish_tick(runtime)
            await asyncio.sleep(0.05)
        await mosquitto.start()
        await eventually(transport.is_connected, within=10)

        # The broker came back empty. A `$description` there now was republished.
        assert await retained(mosquitto.port, f"{root}/$description")
        assert await retained(mosquitto.port, f"{root}/$state") == b"ready"

        await command(mosquitto.port, after_restart, "OPEN")
        await relay_opened_by_command(runtime, after_restart)
    finally:
        await emitter_runtime.stop_clone(runtime)

    ours = [r for r in caplog.records if r.name.startswith("panelbench")]
    warnings = [r for r in ours if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "lost the MQTT broker" in warnings[0].getMessage()
    assert not [r for r in caplog.records if r.exc_info], "a traceback was logged"
    assert any("reconnected to the MQTT broker" in r.getMessage() for r in ours)
