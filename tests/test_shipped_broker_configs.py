"""The add-on's broker and the standalone container's lift mosquitto's per-client queue limit.

One broker carries every panel PanelBench runs, so one subscription to ``ebus/5/#``
is handed every panel's retained tree at once. Mosquitto's default of 1000 queued
messages per client drops the excess over a real network, where the burst drains
slower than it is queued. A client on loopback receives all of it, so the limit is
checked in the configs rather than through a broker.

The add-on's config is the ``<<CONF`` heredoc in ``panelbench/run.sh``, extracted
and expanded by bash rather than restated here. The standalone container's is
``mosquitto/mosquitto.conf.template``, which ``scripts/entrypoint.sh`` copies as it
is. ``scripts/run-local.sh`` is covered in ``test_run_local_brokers.py``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_RUN_SH = _REPO / "panelbench" / "run.sh"
_TEMPLATE = _REPO / "mosquitto" / "mosquitto.conf.template"


def _addon_config() -> str:
    """The config run.sh writes, expanded by bash as the add-on's start expands it."""
    source = _RUN_SH.read_text()
    heredoc = re.search(
        r"^cat > /app/mosquitto/mosquitto\.conf (<<CONF\n.*?^CONF)$",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert heredoc, "run.sh no longer writes /app/mosquitto/mosquitto.conf from a <<CONF heredoc"
    cert_dir = [line for line in source.splitlines() if line.startswith("export CERT_DIR=")]
    assert len(cert_dir) == 1, "run.sh no longer names its certificate directory"

    # Mirrors run.sh: the same `set` flags and the variable the heredoc expands,
    # with the heredoc written to stdout rather than into /app.
    script = f"set -euo pipefail\n{cert_dir[0]}\ncat {heredoc.group(1)}\n"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
    )
    return result.stdout


def _container_config() -> str:
    """The config the standalone container runs, which entrypoint.sh copies unchanged."""
    return _TEMPLATE.read_text()


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(_addon_config, id="add-on"),
        pytest.param(_container_config, id="container"),
    ],
)
def test_the_broker_lifts_the_per_client_queue_limit(config: Callable[[], str]) -> None:
    limits = [line for line in config().splitlines() if line.startswith("max_queued_messages")]

    assert limits == ["max_queued_messages 0"]
