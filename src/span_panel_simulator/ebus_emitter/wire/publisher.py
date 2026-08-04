"""Wire publisher — owns the per-tick diff/publish loop.

The emitter hands the publisher a ``PropertyBag`` representing the current
tick's full property values. The publisher diffs against the previous tick's
state, encodes each changed property's value, and publishes via the MQTT
client seam.

This is the public seam between the emitter facade and the wire layer for
publishing: ``Emitter`` no longer reaches into ``PropertyDiffer`` directly."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import ebus_sdk

from span_panel_simulator.ebus_emitter.wire.graph_builder import BuiltGraph
from span_panel_simulator.ebus_emitter.wire.property_bag import PropertyBag, PropertyDiffer


@runtime_checkable
class _MqttClientLike(Protocol):
    def is_connected(self) -> bool: ...
    async def publish(
        self,
        topic: str,
        payload: bytes,
        qos: int = 0,
        retain: bool = False,
    ) -> None: ...
    async def subscribe(self, topic: str) -> None: ...


class Publisher:
    """Owns the diff/publish loop. Consumers hand it a ``PropertyBag``; it
    computes the delta against the prior tick and publishes changes via the
    SDK seam."""

    def __init__(
        self,
        graph: BuiltGraph,
        mqtt: _MqttClientLike,
        *,
        domain: str,
        bus_version: str,
    ) -> None:
        self._graph = graph
        self._mqtt = mqtt
        self._domain = domain
        self._bus_version = bus_version
        self._differ = PropertyDiffer(all_keys=tuple(graph.properties.keys()))

    async def publish(self, bag: PropertyBag) -> None:
        """Publish all changed properties since the last call."""
        changes = self._differ.diff(bag)
        for key, value in changes:
            sdk_prop = self._graph.properties[key]
            topic = self._topic_for(sdk_prop)
            await self._mqtt.publish(topic, _encode_payload(value), qos=1, retain=True)
        self._differ.commit(changes)

    def _topic_for(self, sdk_prop: ebus_sdk.Property) -> str:
        device_id = sdk_prop.get_device_id()
        node_id = sdk_prop.get_node_id()
        return f"{self._domain}/{self._bus_version}/{device_id}/{node_id}/{sdk_prop.id()}"


def _encode_payload(value: object) -> bytes:
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value).encode()
    return str(value).encode()
