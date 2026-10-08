"""A panel's weather is the same in every process that simulates it.

The live engine seeded its weather with ``hash(serial_number)``, and Python salts
``hash`` of a string per interpreter (``PYTHONHASHSEED``). So each start of
PanelBench gave the same serial different weather: solar output stepped up or
down at every restart, the producer energy estimate moved with it, and neither
matched the history generated for the same days, which was already seeded from a
digest.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from panelbench.solar import weather_seed

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "default_MAIN_40.yaml"

# Noon in the default config's timezone (America/Los_Angeles) on 2024-07-01, so the
# sun is up and the weather factor is what decides the output.
_SUMMER_NOON = 1719860400.0

# Both places the engine seeds from the serial: the live solar curve, and the
# annual estimate that gives an unseeded producer its starting energy.
_PROBE = f"""
import sys

import yaml

from panelbench.engine import RealisticBehaviorEngine

config = yaml.safe_load(open(sys.argv[1]))
engine = RealisticBehaviorEngine(0.0, config)
producer = next(
    t for t in config["circuit_templates"].values()
    if t["energy_profile"]["mode"] == "producer"
)
print(repr(engine.estimate_annual_energy_wh(producer)))
print(repr(engine._apply_solar_day_night_cycle(1000.0, {_SUMMER_NOON})))
"""


def _simulate_in_a_fresh_process(hash_seed: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(_CONFIG)],
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_a_serial_gets_the_same_weather_in_every_process() -> None:
    first, second = (_simulate_in_a_fresh_process(seed) for seed in ("1", "2"))

    assert float(first.splitlines()[1]) > 0, "the probe ran at night and proves nothing"
    assert first == second


def test_the_seed_is_the_one_history_has_always_used() -> None:
    """History already generated for a panel was seeded this way, so the live
    weather now continues it rather than starting a different sky at the join."""
    serial = "example-serial-001"

    assert weather_seed(serial) == int.from_bytes(
        hashlib.sha256(serial.encode("utf-8")).digest()[:8], "big"
    )
