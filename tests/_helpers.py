"""What the tests that run a panel through the emitter share.

A panel here is the shipped default config, optionally modified and written to a
temporary file, built through `start_clone` with a `RecordingTransport` in place
of the MQTT client. That is the assembly a real panel goes through, so what the
recorder holds is what a consumer would replay from the broker.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import yaml

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.wire_capture import RecordingTransport
from panelbench.engine import DynamicSimulationEngine

if TYPE_CHECKING:
    from collections.abc import Mapping

    from panelbench.config_types import SimulationConfig
    from panelbench.emitter_adapter.runtime import CloneRuntime

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


async def clone(config: Path) -> tuple[CloneRuntime, RecordingTransport]:
    """The panel *config* describes, started and publishing into a recorder.

    Nothing has ticked yet: the recorder holds the tree and its descriptions, and
    the first `publish_tick` fills in the values.
    """
    engine = DynamicSimulationEngine(config_path=config)
    await engine.initialize_async()
    recorder = RecordingTransport()
    runtime = await emitter_runtime.start_clone(engine, transport=recorder)
    return runtime, recorder


def published(recorder: RecordingTransport, device_id: str, path: str) -> str:
    """The retained value of *device_id*'s property at *path*."""
    return recorder.retained[f"ebus/5/{device_id}/{path}"].decode()
