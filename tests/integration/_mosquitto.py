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
from pathlib import Path

import pytest

# Ubuntu 26.04's apparmor package confines /usr/sbin/mosquitto with this profile.
APPARMOR_PROFILE = Path("/etc/apparmor.d/mosquitto")

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

    async def start(self, timeout_s: float = 5.0) -> None:
        """Start the broker and wait until it listens, or fail saying why it did not.

        A broker that exits fails with its exit status and its own log. One that does not
        listen within *timeout_s* is stopped first, so no process outlives the failure.
        Ubuntu 26.04's apparmor package confines ``/usr/sbin/mosquitto`` to configs under
        ``/etc/mosquitto``, so there a broker started with this config in a tmp_path
        exits at once; ``tests.yaml`` unloads that profile on CI, and where the profile is
        installed the failure says how to do the same.
        """
        with self._log.open("ab") as log:
            self._process = await asyncio.create_subprocess_exec(
                self._executable, "-c", str(self._config), stdout=log, stderr=subprocess.STDOUT
            )
        process = self._process
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    if process.returncode is not None:
                        raise RuntimeError(self._exited(process.returncode))
                    with contextlib.suppress(OSError):
                        _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                        writer.close()
                        await writer.wait_closed()
                        return
                    await asyncio.sleep(0.02)
        except TimeoutError:
            # A broker that exited during the last poll is reaped already, and a kill
            # would find no process: its exit is the failure.
            if process.returncode is not None:
                raise RuntimeError(self._exited(process.returncode)) from None
            process.kill()
            await process.wait()
            raise RuntimeError(
                self._failure(f"did not listen on {self.port} within {timeout_s:g} s")
            ) from None

    def _exited(self, returncode: int) -> str:
        """The failure of a broker that exited at start with *returncode*."""
        return self._failure(f"exited with {returncode}") + _apparmor_hint()

    def _failure(self, what: str) -> str:
        """*what* went wrong at start, with the broker's own log, which says why."""
        log = self._log.read_text(encoding="utf-8", errors="replace").strip()
        return f"mosquitto ({self._executable}) {what}; its log: {log or '(empty)'}"

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


def _apparmor_hint() -> str:
    """How to free the broker, on a host whose AppArmor profile for mosquitto, if loaded,
    stops it reading a config outside /etc/mosquitto; nothing on any other host. The
    harness does not check whether the profile is loaded: the kernel's list of loaded
    profiles is readable only by root."""
    if not APPARMOR_PROFILE.exists():
        return ""
    return (
        f"\nThis host has an AppArmor profile for mosquitto, {APPARMOR_PROFILE}; if it is "
        "loaded it confines the broker to configs under /etc/mosquitto, so it cannot read "
        "this test's config. `sudo apparmor_parser -R "
        f"{APPARMOR_PROFILE}` unloads it, as CI does, until it is loaded again or the "
        f"machine restarts; `sudo apparmor_parser -r {APPARMOR_PROFILE}` loads it again."
    )
