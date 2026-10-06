"""What the tests that run a panel through the emitter share.

A panel here is the shipped default config, optionally modified and written to a
temporary file, then started by `wire_capture.recorded_panel`. That goes through
the assembly a real panel does, so what the recorder holds is what a consumer
would replay from the broker.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

    from panelbench.config_types import SimulationConfig
    from panelbench.emitter_adapter.wire_capture import RecordingTransport

DEFAULT_CONFIG: Final = Path(__file__).resolve().parents[1] / "configs" / "default_MAIN_40.yaml"

# SPAN firmware strings either side of release 202639, where the BESS meter's sign
# and the EVSE user limit's publication changed.
EARLIER_FIRMWARE: Final = "spanos2/r202633/02"
CURRENT_FIRMWARE: Final = "spanos2/r202639/01"


def default_config() -> SimulationConfig:
    """A fresh copy of the shipped default config, to modify and then write."""
    config: SimulationConfig = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    return config


def write_config(path: Path, config: Mapping[str, object]) -> Path:
    """Write *config* to *path* as YAML and return *path*."""
    path.write_text(yaml.safe_dump(dict(config), sort_keys=False), encoding="utf-8")
    return path


def published(recorder: RecordingTransport, device_id: str, path: str) -> str:
    """The retained value of *device_id*'s property at *path*."""
    return recorder.retained[f"ebus/5/{device_id}/{path}"].decode()
