"""The integration broker says why it did not start, rather than timing out.

On Ubuntu 26.04 the apparmor package confines ``/usr/sbin/mosquitto`` to configs under
``/etc/mosquitto``, so the broker this harness starts with a config in its tmp_path
exits at once. All the harness reported was a five-second timeout.
"""

from __future__ import annotations

import asyncio
import os
import stat
from typing import TYPE_CHECKING

import pytest

from tests.integration import _mosquitto
from tests.integration._mosquitto import Mosquitto

if TYPE_CHECKING:
    from pathlib import Path


async def test_a_broker_that_exits_at_start_reports_its_exit_and_log(tmp_path: Path) -> None:
    broker_bin = tmp_path / "broker"
    broker_bin.write_text(
        "#!/bin/sh\necho 'Error: Unable to open config file'\nexit 3\n", encoding="utf-8"
    )
    broker_bin.chmod(broker_bin.stat().st_mode | stat.S_IXUSR)

    broker = Mosquitto(str(broker_bin), tmp_path)

    with pytest.raises(RuntimeError, match=r"exited with 3.*Unable to open config file"):
        await broker.start()


async def test_a_broker_that_never_listens_is_stopped_and_reported(tmp_path: Path) -> None:
    """The start fails within its timeout, and leaves no broker running behind it."""
    pid_file = tmp_path / "broker.pid"
    broker_bin = tmp_path / "broker"
    broker_bin.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n", encoding="utf-8")
    broker_bin.chmod(broker_bin.stat().st_mode | stat.S_IXUSR)
    broker = Mosquitto(str(broker_bin), tmp_path)

    async with asyncio.timeout(5):
        with pytest.raises(RuntimeError, match=r"did not listen"):
            await broker.start(timeout_s=1.0)

    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text(encoding="utf-8")), 0)


async def test_a_broker_that_exits_as_the_timeout_fires_is_reported_as_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broker exits while the harness is still waiting to connect, and the timeout
    fires after asyncio has reaped it: the failure is its exit, not a failed kill."""

    async def never_connects(host: str, port: int) -> tuple[object, object]:
        await asyncio.sleep(2)
        raise ConnectionRefusedError

    monkeypatch.setattr(asyncio, "open_connection", never_connects)
    broker_bin = tmp_path / "broker"
    broker_bin.write_text(
        "#!/bin/sh\nsleep 0.05\necho 'Error: Unable to open config file'\nexit 3\n",
        encoding="utf-8",
    )
    broker_bin.chmod(broker_bin.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(RuntimeError, match=r"exited with 3.*Unable to open config file"):
        await Mosquitto(str(broker_bin), tmp_path).start(timeout_s=0.5)


async def test_where_apparmor_confines_mosquitto_the_failure_says_how_to_free_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "apparmor-mosquitto"
    profile.write_text("profile mosquitto /usr/sbin/mosquitto {}\n", encoding="utf-8")
    monkeypatch.setattr(_mosquitto, "APPARMOR_PROFILE", profile)
    broker_bin = tmp_path / "broker"
    broker_bin.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    broker_bin.chmod(broker_bin.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(RuntimeError, match=r"AppArmor.*apparmor_parser -R"):
        await Mosquitto(str(broker_bin), tmp_path).start()
