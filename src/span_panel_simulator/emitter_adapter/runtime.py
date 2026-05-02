"""Per-clone wiring: assemble emitter from clone profile, drive ticks, handle dashboard
controls.

This module is the single integration point between the simulator's tick loop and the
external ``ebus_emitter`` package. ``start_clone`` builds the manifest + runtime spec
from a clone profile, constructs the emitter, opens the per-clone MQTT client, and
publishes the cold-start lifecycle. ``on_tick`` is called from the simulator's
scheduler each cycle and returns the resulting ``EbusPanelSnapshot``.

Dashboard helpers (``force_grid_offline``, ``force_grid_online``, ``reset_property_override``)
expose the emitter's debug controls to the UI without leaking the emitter's API surface
into the dashboard layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiomqtt
from ebus_emitter import (
    DeviceManifest,
    EbusPanelSnapshot,
    Emitter,
    RuntimeSpec,
    SetterRegistry,
)

from span_panel_simulator.emitter_adapter import setter_handlers, spec_generator


@dataclass(slots=True)
class CloneRuntime:
    clone_profile: dict[str, Any]
    manifest: DeviceManifest
    runtime_spec: RuntimeSpec
    setters: SetterRegistry
    mqtt: Any
    emitter: Emitter


class _AiomqttPublisher:
    """Adapter wrapping ``aiomqtt.Client`` to satisfy the emitter's duck-typed
    MQTT interface (``is_connected``, ``publish``, ``subscribe``).

    Replace with ``ebus_mqtt_client.MqttClient`` when the producer adopts the
    org-standard transport client. For v0.1.0 of the integration this minimal adapter
    keeps the existing aiomqtt-based simulator wiring intact."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        will: aiomqtt.Will | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._will = will
        self._client: aiomqtt.Client | None = None

    async def connect(self) -> None:
        self._client = aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            identifier=self._client_id,
            username=self._username,
            password=self._password,
            will=self._will,
        )
        await self._client.__aenter__()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    def is_connected(self) -> bool:
        return self._client is not None

    async def publish(
        self,
        topic: str,
        payload: bytes,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        assert self._client is not None
        await self._client.publish(topic, payload=payload, qos=qos, retain=retain)

    async def subscribe(self, topic: str) -> None:
        assert self._client is not None
        await self._client.subscribe(topic)


async def start_clone(clone_profile: dict[str, Any]) -> CloneRuntime:
    """Build the emitter for one clone, connect MQTT, run the cold-start lifecycle.
    Returns a CloneRuntime the simulator's scheduler holds across ticks."""
    artifacts = spec_generator.generate(clone_profile)

    setters = SetterRegistry()
    runtime = CloneRuntime(
        clone_profile=clone_profile,
        manifest=artifacts.manifest,
        runtime_spec=artifacts.runtime_spec,
        setters=setters,
        mqtt=None,  # set below
        emitter=None,  # set below
    )
    setter_handlers.register_all(setters, runtime)

    lwt_topic, lwt_payload, lwt_qos, lwt_retain = Emitter.lwt_settings(artifacts.manifest)
    will = aiomqtt.Will(
        topic=lwt_topic,
        payload=lwt_payload,
        qos=lwt_qos,
        retain=lwt_retain,
    )

    broker_cfg = clone_profile.get("broker", {}) or {}
    panel_id = clone_profile["panel_config"]["serial_number"]
    mqtt = _AiomqttPublisher(
        host=broker_cfg.get("host", "127.0.0.1"),
        port=int(broker_cfg.get("port", 1883)),
        client_id=f"span-sim-{panel_id}",
        username=broker_cfg.get("username"),
        password=broker_cfg.get("password"),
        will=will,
    )
    await mqtt.connect()

    emitter = Emitter(artifacts.manifest, artifacts.runtime_spec, setters, mqtt)
    runtime.mqtt = mqtt
    runtime.emitter = emitter

    await emitter.start()
    return runtime


async def on_tick(runtime: CloneRuntime) -> EbusPanelSnapshot:
    """Advance the emitter one tick. The emitter handles its own publish; this returns
    the snapshot for the simulator's UI/history bridge."""
    return await runtime.emitter.tick()


async def stop_clone(runtime: CloneRuntime, *, graceful: bool = True) -> None:
    try:
        await runtime.emitter.stop(graceful=graceful)
    finally:
        await runtime.mqtt.disconnect()


async def restart_clone(runtime: CloneRuntime) -> CloneRuntime:
    """Tear down and rebuild the emitter — used when a runtime-spec mutation requires
    emitter reconstruction (BESS schedule change, circuit add/remove, priority shift)."""
    await stop_clone(runtime, graceful=True)
    return await start_clone(runtime.clone_profile)


# -- Dashboard helpers ----------------------------------------------------------


async def force_grid_offline(runtime: CloneRuntime) -> None:
    """Dashboard-driven grid kill — sticky until released or restart."""
    await runtime.emitter.force_grid_state("OFFLINE")


async def force_grid_online(runtime: CloneRuntime) -> None:
    """Release a force-offline. No-op if no force is active."""
    await runtime.emitter.force_grid_state(None)


async def reset_property_override(
    runtime: CloneRuntime,
    entity_class: str,
    instance_id: str,
    property_path: str,
) -> None:
    """Dashboard-driven explicit clear of an override."""
    await runtime.emitter.clear_property_override(entity_class, instance_id, property_path)
