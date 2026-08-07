"""Ways of obtaining $description documents.

Both feeds return the same shape - device id to raw description document - so the rules
run unchanged over an in-process tree or a capture off a live broker. That is the point:
the in-process feed catches defects at authoring time, the capture feed proves the wire
matches what we composed.

No live-broker mode. It would drag credentials and a running broker into CI, be
non-deterministic, and weld the checker to one transport.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


class CaptureError(ValueError):
    """A capture file that cannot be read."""


class Described(Protocol):
    """Anything that can report a Homie id and description.

    A Protocol rather than an import: it is structurally satisfied by ``ebus_sdk.Device``
    without this package depending on the SDK.
    """

    def id(self) -> str: ...

    def description(self) -> dict[str, object]: ...


def from_devices(devices: Iterable[Described]) -> dict[str, object]:
    """Description documents straight from a built device tree, no broker involved."""
    return {device.id(): device.description() for device in devices}


def from_capture(path: Path) -> dict[str, object]:
    """Description documents from a capture written by scripts/check-conformance.py."""
    parsed: object = json.loads(path.read_text())
    if not isinstance(parsed, dict):
        raise CaptureError(
            f"{path}: expected a mapping of device id to description document, "
            f"got {type(parsed).__name__}"
        )
    documents: dict[str, object] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            raise CaptureError(f"{path}: non-string device id {key!r}")
        documents[key] = value
    return documents
