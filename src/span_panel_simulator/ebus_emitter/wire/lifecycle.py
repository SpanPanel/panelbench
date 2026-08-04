"""Lifecycle controller — owns $state, $description, /set subscription, LWT.

v1_flat behaviour only; v2_children adds child-device cascade in a future major release.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from span_panel_simulator.flat_emitter.exceptions import EmitterStateError
from span_panel_simulator.flat_emitter.manifest import DeviceManifest
from span_panel_simulator.flat_emitter.wire.graph_builder import BuiltGraph
from span_panel_simulator.flat_emitter.wire.mapping_loader import MappingTable
from span_panel_simulator.flat_emitter.wire.profile_loader import ProfileTable
from span_panel_simulator.flat_emitter.wire.set_router import SetSubscription
from span_panel_simulator.flat_emitter.wire.wire_paths import (
    device_description_topic,
    device_state_topic,
    root_state_topic,
)


@runtime_checkable
class _MqttClientLike(Protocol):
    def is_connected(self) -> bool: ...
    async def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> None: ...
    async def subscribe(self, topic: str) -> None: ...


def lwt_settings(
    manifest: DeviceManifest,
    *,
    domain: str,
    bus_version: str,
    root_entity_class: str,
) -> tuple[str, bytes, int, bool]:
    root = manifest.of_class(root_entity_class)[0]
    return root_state_topic(domain, bus_version, root.instance_id), b"lost", 1, True


@dataclass(slots=True)
class LifecycleController:
    manifest: DeviceManifest
    mapping: MappingTable
    profiles: ProfileTable
    graph: BuiltGraph
    mqtt: _MqttClientLike
    domain: str = "ebus"
    bus_version: str = "5"
    subscriptions: list[SetSubscription] = field(default_factory=list)
    _started: bool = False
    _stopped: bool = False
    _root_id: str = ""

    def __post_init__(self) -> None:
        root_ec = self.mapping.root_entity_class()
        self._root_id = self.manifest.of_class(root_ec)[0].instance_id

    async def start(self) -> None:
        if not self.mqtt.is_connected():
            raise EmitterStateError("mqtt_client must be connected before start()")

        await self.mqtt.publish(
            root_state_topic(self.domain, self.bus_version, self._root_id),
            b"init",
            qos=1,
            retain=True,
        )

        for device_id, payload in self.graph.description_payloads.items():
            await self.mqtt.publish(
                device_description_topic(self.domain, self.bus_version, device_id),
                json.dumps(payload).encode(),
                qos=1,
                retain=True,
            )

        for sub in self.subscriptions:
            await self.mqtt.subscribe(sub.topic_pattern)

        await self.mqtt.publish(
            root_state_topic(self.domain, self.bus_version, self._root_id),
            b"ready",
            qos=1,
            retain=True,
        )
        self._started = True

    async def stop(self, *, graceful: bool, clear_retained: bool = False) -> None:
        if not graceful:
            return
        for device_id in self.graph.devices:
            if device_id == self._root_id:
                continue
            await self.mqtt.publish(
                device_state_topic(self.domain, self.bus_version, device_id),
                b"disconnected",
                qos=1,
                retain=True,
            )
        await self.mqtt.publish(
            root_state_topic(self.domain, self.bus_version, self._root_id),
            b"disconnected",
            qos=1,
            retain=True,
        )
        if clear_retained:
            for topic in self._retained_topics():
                await self.mqtt.publish(topic, b"", qos=1, retain=True)
        self._stopped = True

    def _retained_topics(self) -> list[str]:
        topics = [
            root_state_topic(self.domain, self.bus_version, self._root_id),
            *(
                device_state_topic(self.domain, self.bus_version, device_id)
                for device_id in self.graph.devices
                if device_id != self._root_id
            ),
            *(
                device_description_topic(self.domain, self.bus_version, device_id)
                for device_id in self.graph.description_payloads
            ),
        ]
        topics.extend(
            f"{self.domain}/{self.bus_version}/{prop.get_device_id()}/"
            f"{prop.get_node_id()}/{prop.id()}"
            for prop in self.graph.properties.values()
        )
        return sorted(set(topics))
