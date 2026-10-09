"""Each BROKER_PORT run by ``scripts/run-local.sh`` is a broker of its own.

A real panel carries its own broker. Panels sharing one hand every subscriber all
of their retained trees at once, and a large tree beside its neighbours overruns
the per-client queue that a broker of its own never reaches.

The broker setup and status functions are extracted from the shipped script and
executed, rather than restated here, so the test follows the script if it drifts.
``mosquitto_passwd`` is replaced with a stub on PATH.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_RUN_LOCAL = Path(__file__).parent.parent / "scripts" / "run-local.sh"


def _function(source: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{.*?^\}}", source, re.MULTILINE | re.DOTALL)
    assert match, f"run-local.sh no longer defines {name}()"
    return match.group(0)


def _broker_paths(source: str) -> str:
    lines = [
        line
        for line in source.splitlines()
        if line.startswith(("MOSQUITTO_DIR=", "MOSQUITTO_PID_FILE="))
    ]
    assert len(lines) == 2, "run-local.sh no longer names its broker directory and PID file"
    return "\n".join(lines)


def _run(repo_dir: Path, body: str, broker_port: int) -> str:
    stub_dir = repo_dir / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "mosquitto_passwd"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    source = _RUN_LOCAL.read_text()
    script = (
        "set -euo pipefail\n"
        f'REPO_DIR="{repo_dir}"\n'
        'CERT_DIR="${REPO_DIR}/.local/certs"\n'
        'PID_DIR="${REPO_DIR}/.local/pids"\n'
        "BROKER_USERNAME=span\nBROKER_PASSWORD=pw\n"
        f"BROKER_PORT={broker_port}\n"
        f"{_broker_paths(source)}\n"
        f"{_function(source, 'setup_mosquitto')}\n"
        f"{_function(source, 'show_status')}\n"
        f"{body}\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": f"{stub_dir}:/usr/bin:/bin"},
        check=True,
    )
    return result.stdout


def test_each_broker_port_gets_its_own_config_and_pid_file(tmp_path: Path) -> None:
    _run(tmp_path, "setup_mosquitto", 28881)
    _run(tmp_path, "setup_mosquitto", 28882)

    configs = {
        port: (tmp_path / ".local" / f"mosquitto-{port}" / "mosquitto.conf").read_text()
        for port in (28881, 28882)
    }
    for port, config in configs.items():
        assert f"listener {port}\n" in config
        assert f"pid_file {tmp_path}/.local/pids/mosquitto-{port}.pid\n" in config
        assert f"password_file {tmp_path}/.local/mosquitto-{port}/passwd\n" in config


def test_status_lists_every_running_broker(tmp_path: Path) -> None:
    pid_dir = tmp_path / ".local" / "pids"
    pid_dir.mkdir(parents=True)
    # This process stands in for a running broker: `kill -0` succeeds on it.
    for port in (28881, 28882):
        (pid_dir / f"mosquitto-{port}.pid").write_text(f"{os.getpid()}\n")

    status = _run(tmp_path, "show_status", 28881)

    assert f"mosquitto-28881: running (pid {os.getpid()})" in status
    assert f"mosquitto-28882: running (pid {os.getpid()})" in status
