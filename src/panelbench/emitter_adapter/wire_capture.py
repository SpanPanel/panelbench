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

from typing import TYPE_CHECKING

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.engine import DynamicSimulationEngine

if TYPE_CHECKING:
    import pathlib

# Homie topics are `ebus/<version>/<device-id>/<rest...>`; the device id is the
# third segment and everything after it is the key a consumer sees.
_DEVICE_SEGMENT = 2
_MIN_SEGMENTS = 4


class RecordingPublisher:
    """Satisfies `MqttPublisher`, keeping the last payload seen per topic.

    Last-wins rather than an append-only log, because that is what a broker's
    retained store holds and therefore what a consumer replays on connect. A
    value corrected within a single tick should leave only the correction here.
    """

    def __init__(self) -> None:
        self.retained: dict[str, bytes] = {}

    def is_connected(self) -> bool:
        return True

    async def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> None:
        del qos, retain
        self.retained[topic] = payload

    async def subscribe(self, topic: str) -> None:
        del topic

    async def disconnect(self) -> None:
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


async def capture(config: pathlib.Path) -> dict[str, dict[str, str]]:
    """Run one panel through the real assembly and return what it published.

    Goes through `start_clone` with the MQTT client substituted, rather than
    reassembling the emitter here: a capture taken through different wiring than
    a real panel uses proves less than it appears to.
    """
    engine = DynamicSimulationEngine(config_path=config)
    await engine.initialize_async()

    recorder = RecordingPublisher()
    runtime = await emitter_runtime.start_clone(engine, publisher=recorder)
    # start() publishes the tree and its descriptions; one tick fills in values.
    await emitter_runtime.publish_tick(runtime)

    return as_capture(recorder.retained)
