"""Capture the full retained surface a consumer sees, not just declarations.

The conformance checker works from `$description` documents, because conformance
is a question about what a device declares. That is not enough to exercise a
*consumer*: a parser fed only descriptions can be asked whether it understands
the shape of a panel, never whether it builds the right snapshot from one — which
is the part that reaches a user.

So this captures descriptions, `$state`, and every property value, keyed the way
a consumer receives them.

It lives in the package rather than in `scripts/` so it can be imported by a test
that checks the committed capture still matches what the emitter emits. A capture
stranded in a script is one nobody notices going stale.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ebus_sdk import DiscoveredDevice

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.engine import DynamicSimulationEngine

if TYPE_CHECKING:
    import pathlib

    from panelbench.emitter_adapter.runtime import CloneRuntime

# Homie topics are `ebus/<version>/<device-id>/<rest...>`; the device id is the
# third segment and everything after it is the key a consumer sees.
_DEVICE_SEGMENT = 2
_MIN_SEGMENTS = 4


class RecordingTransport:
    """Satisfies `MqttDeviceTransport`, keeping the last payload seen per topic.

    Last-wins rather than an append-only log, because that is what a broker's
    retained store holds and therefore what a consumer replays on connect. A
    value corrected within a single tick should leave only the correction here.

    Synchronous, like the contract it implements. There is no queue and no client
    behind it, so a publish is complete the moment it returns — which is what
    makes a capture a plain function call rather than something to await and
    drain.
    """

    is_running = True

    def __init__(self) -> None:
        self.retained: dict[str, bytes] = {}

    def is_connected(self) -> bool:
        return True

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> object:
        del qos, retain
        self.retained[topic] = str(data).encode()
        return None

    def subscribe(self, sub: str, param: object = None, qos: int = 1) -> object:
        del sub, param, qos
        return None


def as_capture(retained: dict[str, bytes]) -> dict[str, dict[str, str]]:
    """Regroup flat topics into the device-keyed shape a consumer sees."""
    devices: dict[str, dict[str, str]] = {}
    for topic, payload in sorted(retained.items()):
        parts = topic.split("/")
        if len(parts) < _MIN_SEGMENTS:
            continue
        devices.setdefault(parts[_DEVICE_SEGMENT], {})["/".join(parts[_DEVICE_SEGMENT + 1 :])] = (
            payload.decode()
        )
    return devices


async def recorded_panel(
    config: pathlib.Path, *, grid_online: bool = True
) -> tuple[CloneRuntime, RecordingTransport]:
    """The panel *config* describes, started and publishing into a recorder.

    Goes through `start_clone` with the MQTT client substituted, rather than
    reassembling the emitter here: a capture taken through different wiring than
    a real panel uses proves less than it appears to. *grid_online* is set on the
    engine before the emitter starts, as the dashboard's grid toggle sets it.

    Nothing has ticked yet: the recorder holds the tree and its descriptions, and
    the first `publish_tick` fills in the values.
    """
    engine = DynamicSimulationEngine(config_path=config)
    await engine.initialize_async()
    engine.set_grid_online(grid_online)

    recorder = RecordingTransport()
    runtime = await emitter_runtime.start_clone(engine, transport=recorder)
    return runtime, recorder


async def capture_retained(config: pathlib.Path, *, grid_online: bool = True) -> dict[str, bytes]:
    """Run one panel through the real assembly and return its retained topics.

    The panel is `recorded_panel`'s, after one tick: `start()` publishes the tree
    and its descriptions, and the tick fills in the values.
    """
    runtime, recorder = await recorded_panel(config, grid_online=grid_online)
    await emitter_runtime.publish_tick(runtime)
    return recorder.retained


async def capture(config: pathlib.Path, *, grid_online: bool = True) -> dict[str, dict[str, str]]:
    """`capture_retained`, regrouped the way a consumer sees it."""
    return as_capture(await capture_retained(config, grid_online=grid_online))


def discovered_devices(retained: Mapping[str, bytes]) -> dict[str, DiscoveredDevice]:
    """Retained topics as the devices a controller would have discovered.

    The shape `clone.translate_panel_tree` reads, so a tree published here — by
    PanelBench or by the upstream emitter — is read by the same rule as a live
    panel. Only `$description` and `<node>/<property>` topics carry what the
    translator reads; `$state`, `/set` and `$target` topics are skipped.
    """
    devices: dict[str, DiscoveredDevice] = {}
    values: list[tuple[str, str, str, str]] = []
    for topic, payload in sorted(retained.items()):
        parts = topic.split("/")
        if len(parts) < _MIN_SEGMENTS:
            continue
        device_id, rest = parts[_DEVICE_SEGMENT], parts[_DEVICE_SEGMENT + 1 :]
        if rest == ["$description"]:
            devices.setdefault(device_id, DiscoveredDevice(device_id)).update_description(
                payload.decode()
            )
        elif len(rest) == 2 and not any(part.startswith("$") for part in rest):
            values.append((device_id, rest[0], rest[1], payload.decode()))
    for device_id, node, prop, value in values:
        if device_id in devices:
            devices[device_id].update_property(node, prop, value)
    return devices
