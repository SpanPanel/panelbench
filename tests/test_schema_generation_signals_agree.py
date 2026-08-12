"""The two schema-generation signals must say the same thing.

The eBus migration guide's "Schema-generation detection" specifies one rule carried
on two transports: MQTT ``info/data-model-version`` absent = flat, present =
parent/child; REST ``dataModelVersion`` absent = flat, exactly mirroring the MQTT
signal. A consumer may read either.

panelbench published the MQTT half and not the REST half, and nothing failed.
``span-panel-api`` dispatches on the REST value -- it has to, because the adapter
decides which topics to subscribe to and so must exist before the first SUBSCRIBE --
so it read no version, selected the **flat** parser, and read a v1.0 tree with it.
Home Assistant reported a clean startup and built its entities on values parsed
against the wrong vocabulary. The upgrade simply never appeared to happen.

Testing either signal alone would not have caught it: each was internally consistent.
Only their agreement is the property worth holding, so that is what this asserts.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import yaml
from ebus_panel_sim import Emitter, SetterRegistry, TickInputs

from panelbench.emitter_adapter.spec_generator import build_manifest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "src/panelbench/data/homie_schema.json"
_CONFIG = _ROOT / "configs/default_MAIN_40.yaml"


class _Transport:
    """Shaped to ``ebus_sdk.MqttDeviceTransport``: synchronous, ``str`` payloads."""

    is_running = True

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> object:
        self.published.append((topic, data))
        return None

    def subscribe(self, sub: str, param: Any = None, qos: int = 1) -> object:
        return None

    def is_connected(self) -> bool:
        return True


def _rest_data_model_version() -> str | None:
    """What ``GET /api/v2/homie/schema`` advertises.

    Read from the bundled document because ``bootstrap._handle_schema`` serves it
    verbatim; the size-specific render only rewrites the circuit ``space`` format and
    the ``types`` hash, so this is the value that reaches a consumer.
    """
    version = json.loads(_SCHEMA.read_text()).get("dataModelVersion")
    return None if version is None else str(version)


def _mqtt_data_model_version() -> str | None:
    """What the panel device publishes on ``info/data-model-version``."""
    config = yaml.safe_load(_CONFIG.read_text())
    transport = _Transport()
    emitter = Emitter(build_manifest(config), SetterRegistry(), mqttc=transport)
    emitter.start()
    emitter.publish_tick(TickInputs(current_time=0.0, grid_online=True, circuits={}))
    suffix = f"/{config['panel_config']['serial_number']}/info/data-model-version"
    for topic, payload in transport.published:
        if topic.endswith(suffix):
            return payload
    return None


def test_rest_and_mqtt_advertise_the_same_schema_generation() -> None:
    """The mirror rule, asserted directly.

    Equality is the point. Asserting each is ``"1.0"`` separately would pass while a
    future bump moved one and not the other -- the same silent misdispatch in a new
    costume.
    """
    rest = _rest_data_model_version()
    mqtt = _mqtt_data_model_version()

    assert rest == mqtt, (
        f"REST advertises dataModelVersion={rest!r} while MQTT publishes "
        f"info/data-model-version={mqtt!r}. A consumer dispatching on either one would "
        "select a different parser depending on which it read."
    )


def test_panelbench_advertises_the_parent_child_generation() -> None:
    """Absence is a signal, not a default.

    Pinned so the key cannot quietly go missing again: the guide reads an absent
    version as *flat*, so dropping it raises no error anywhere -- it produces a v1.0
    panel that every consumer parses as flat.
    """
    assert _rest_data_model_version() == "1.0"
