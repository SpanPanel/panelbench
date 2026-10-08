"""A real mosquitto for the integration tests, which they can stop, restart and freeze.

Skipped where mosquitto is not installed, unless ``PANELBENCH_REQUIRE_MOSQUITTO``
is set, as CI sets it: there a missing broker fails rather than passing silently.
The in-process amqtt broker the other tests use cannot stand in: a restart has to
take the process away, sockets and retained store with it, and a freeze has to
stop it answering while its sockets stay open.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import socket
import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# Package managers put mosquitto in an sbin directory, which is often not on PATH.
MOSQUITTO = shutil.which(
    "mosquitto",
    path=os.pathsep.join(
        [os.environ.get("PATH", ""), "/opt/homebrew/sbin", "/usr/local/sbin", "/usr/sbin"]
    ),
)

_REQUIRED = os.environ.get("PANELBENCH_REQUIRE_MOSQUITTO", "") not in ("", "0")

requires_mosquitto = pytest.mark.skipif(
    MOSQUITTO is None and not _REQUIRED, reason="mosquitto is not installed"
)


class Mosquitto:
    """A mosquitto process on a fixed port that can be stopped and started again.

    No persistence, so a restart empties the retained store the way a broker
    that lost it would: only a republished tree can refill it.
    """

    def __init__(self, executable: str, workdir: Path, *, allow_anonymous: bool = True) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port: int = probe.getsockname()[1]
        self._executable = executable
        self._config = workdir / "mosquitto.conf"
        self._config.write_text(
            f"listener {self.port} 127.0.0.1\n"
            f"allow_anonymous {'true' if allow_anonymous else 'false'}\npersistence false\n",
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

    def freeze(self) -> None:
        """Stop the broker answering, its sockets left open: a hung broker, as a
        sleeping host or a dropped link presents to the client."""
        assert self._process is not None
        self._process.send_signal(signal.SIGSTOP)

    def thaw(self) -> None:
        assert self._process is not None
        self._process.send_signal(signal.SIGCONT)

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        process.send_signal(signal.SIGCONT)  # a frozen process cannot act on SIGTERM
        process.terminate()
        await process.wait()
