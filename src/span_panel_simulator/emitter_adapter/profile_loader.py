"""Load a clone-profile YAML into the dict shape ``spec_generator`` consumes.

Encapsulates the YAML read + the simulator's ``sim-`` prefix convention for serial
numbers. Lives in the simulator-side adapter package because it carries SPAN-specific
naming policy."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml


async def load_clone_profile(path: Path) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, _load_sync, path)
    return raw


def _load_sync(path: Path) -> dict[str, Any]:
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"clone profile {path} did not parse to a dict")
    panel = data.get("panel_config")
    if not isinstance(panel, dict) or "serial_number" not in panel:
        raise ValueError(f"clone profile {path} missing panel_config.serial_number")
    serial = str(panel["serial_number"])
    if not serial.lower().startswith("sim-"):
        panel["serial_number"] = f"sim-{serial}"
    return data
