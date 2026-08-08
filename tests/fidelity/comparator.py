"""Structural comparison of two producers over one config.

Conformance asks whether everything a producer publishes is legal, and the
specification permits omission — so a producer that publishes almost nothing is
perfectly conformant. This asks the other question: does panelbench publish what
the reference producer publishes?

Compares **structure and metadata only**. Numeric payloads and timestamps vary by
design between the two and are never grounds for a failure; what is compared is
which devices exist, and which properties each declares.

Devices are aligned by declared ``type`` and ``name``, never by instance id. The
reference hashes ids with ``sha256("panel-sim-example:" + id)[:32]`` while
panelbench uses ``uuid5``, and panelbench additionally prefixes the panel serial
with ``sim-``, so an id-keyed diff reports every device as a mismatch and nothing
useful.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ebus_panel_sim import Emitter, SetterRegistry

from panelbench.emitter_adapter.wire_capture import capture

FIXTURES = Path(__file__).parent / "fixtures"
UPSTREAM = FIXTURES / "upstream"
REFERENCE_CONFIG = UPSTREAM / "forty_tab_minimal.yaml"
REFERENCE_RUNNER = UPSTREAM / "run_forty_tab_minimal.py"

_REPO = Path(__file__).resolve().parent.parent.parent
PANELBENCH_CONFIG = _REPO / "configs" / "default_MAIN_40.yaml"
"""The tracked 40-tab template, extended to a superset both producers read.

Not a fixture copy. The reference runner reads a narrow slice of the YAML and
ignores the rest, and panelbench loads with ``yaml.safe_load`` into TypedDicts,
so each side takes the keys it understands from one file. A second copy under
``fixtures/`` would drift, and the rich cell would then measure a config nobody
runs.
"""

# Homie topics are `ebus/<version>/<device-id>/<rest...>`.
_DEVICE_SEGMENT = 2
_MIN_SEGMENTS = 4


class RecordingTransport:
    """Bring-your-own-transport recorder for the reference emitter.

    This is why the BYO work came first: without an injectable transport the
    reference emitter can only publish to a real broker, which would make this a
    integration test needing mosquitto rather than a unit one.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []
        self.is_running = True

    def is_connected(self) -> bool:
        return True

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> object:
        self.published.append((topic, str(data), qos, retain))
        return None

    def subscribe(self, sub: str, param: object = None, qos: int = 1) -> object:
        return None


def _load_reference_runner() -> Any:
    """Import the vendored example script as a module.

    Its manifest builders are pure functions over the parsed YAML, and they are
    the reference's own reading of that config. Reimplementing them would compare
    our interpretation against our emitter and call it fidelity.
    """
    spec = importlib.util.spec_from_file_location("_fidelity_reference", REFERENCE_RUNNER)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot import vendored reference runner at {REFERENCE_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_fidelity_reference"] = module
    spec.loader.exec_module(module)
    return module


def _regroup(published: list[tuple[str, str, int, bool]]) -> dict[str, dict[str, str]]:
    """Retained topics, grouped by device id, keyed the way a consumer sees them."""
    devices: dict[str, dict[str, str]] = {}
    for topic, data, _qos, retain in published:
        if not retain:
            continue
        parts = topic.split("/")
        if len(parts) < _MIN_SEGMENTS:
            continue
        devices.setdefault(parts[_DEVICE_SEGMENT], {})["/".join(parts[_DEVICE_SEGMENT + 1 :])] = (
            data
        )
    return devices


def capture_reference(config: Path = REFERENCE_CONFIG) -> dict[str, dict[str, str]]:
    """Run the reference emitter over *config* and return its retained tree."""
    runner = _load_reference_runner()
    profile = runner._load_profile(config)
    manifest = runner._build_manifest(profile)
    bess_config = runner._build_bess_config(profile)

    transport = RecordingTransport()
    emitter = Emitter(
        manifest,
        SetterRegistry(),
        mqttc=transport,
        bess_configs=(bess_config,) if bess_config is not None else (),
    )
    emitter.start()
    for tick in runner._ticks(profile):
        emitter.publish_tick(tick)
    return _regroup(transport.published)


async def capture_panelbench(config: Path = REFERENCE_CONFIG) -> dict[str, dict[str, str]]:
    """Run panelbench over *config* through its real clone assembly."""
    return await capture(config)


def role_of(device_id: str, properties: dict[str, str]) -> str:
    """A stable cross-producer identity: declared ``type::name``.

    Falls back to the raw id when a device published no parsable
    ``$description``, which is itself worth surfacing as a mismatch rather than
    hiding behind a shared placeholder.
    """
    description = properties.get("$description")
    if not description:
        return f"<no-description>::{device_id}"
    try:
        parsed = json.loads(description)
    except json.JSONDecodeError:
        return f"<unparsable-description>::{device_id}"
    return f"{parsed.get('type', '?')}::{parsed.get('name', device_id)}"


def by_role(devices: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {role_of(did, props): props for did, props in devices.items()}


def declared_properties(body: dict[str, str]) -> set[str]:
    """``node/property`` keys a device announces in its ``$description``."""
    description = body.get("$description")
    if not description:
        return set()
    try:
        nodes = json.loads(description).get("nodes") or {}
    except json.JSONDecodeError:
        return set()
    return {
        f"{node_id}/{prop}"
        for node_id, node in nodes.items()
        for prop in (node.get("properties") or {})
    }


def class_of(body: dict[str, str]) -> str:
    """The trailing segment of the declared device type, e.g. ``lugs``, ``mid``."""
    description = body.get("$description")
    if not description:
        return "?"
    try:
        return str(json.loads(description).get("type", "?")).rsplit(".", 1)[-1]
    except json.JSONDecodeError:
        return "?"


@dataclass(frozen=True)
class ParityReport:
    """What panelbench fails to publish, and what it publishes beyond the reference."""

    missing_devices: dict[str, int] = field(default_factory=dict)
    """role -> number of topics the reference publishes for it."""

    extra_devices: dict[str, int] = field(default_factory=dict)

    missing_properties: dict[str, list[str]] = field(default_factory=dict)
    """role -> property keys present in the reference and absent here."""

    extra_properties: dict[str, list[str]] = field(default_factory=dict)

    @property
    def missing_topic_count(self) -> int:
        return sum(self.missing_devices.values()) + sum(
            len(v) for v in self.missing_properties.values()
        )

    def as_baseline(self) -> dict[str, Any]:
        """The JSON-comparable form committed as the baseline."""
        return {
            "missing_devices": self.missing_devices,
            "extra_devices": self.extra_devices,
            "missing_properties": self.missing_properties,
            "extra_properties": self.extra_properties,
        }

    def describe(self) -> str:
        lines: list[str] = []
        for role, count in sorted(self.missing_devices.items()):
            lines.append(f"  device MISSING: {role}  [{count} topics]")
        for role, count in sorted(self.extra_devices.items()):
            lines.append(f"  device EXTRA:   {role}  [{count} topics]")
        for role, keys in sorted(self.missing_properties.items()):
            for key in keys:
                lines.append(f"  missing: {role}  {key}")
        for role, keys in sorted(self.extra_properties.items()):
            for key in keys:
                lines.append(f"  extra:   {role}  {key}")
        return "\n".join(lines) or "  (no structural difference)"


def compare(
    reference: dict[str, dict[str, str]], subject: dict[str, dict[str, str]]
) -> ParityReport:
    """Structural diff of *subject* against *reference*, aligned by role."""
    ref = by_role(reference)
    sub = by_role(subject)

    shared = set(ref) & set(sub)
    return ParityReport(
        missing_devices={r: len(ref[r]) for r in sorted(set(ref) - set(sub))},
        extra_devices={r: len(sub[r]) for r in sorted(set(sub) - set(ref))},
        missing_properties={
            r: sorted(set(ref[r]) - set(sub[r]))
            for r in sorted(shared)
            if set(ref[r]) - set(sub[r])
        },
        extra_properties={
            r: sorted(set(sub[r]) - set(ref[r]))
            for r in sorted(shared)
            if set(sub[r]) - set(ref[r])
        },
    )
