"""Per-clone wiring between simulator engine and emitter wire layer.

``start_clone`` builds the manifest from the engine's config, instantiates an
``Emitter`` (with optional BESS + load-shedding native-device configs), opens the
per-clone MQTT client, runs the cold-start lifecycle, and returns a ``CloneRuntime``
the simulator's panel instance holds across ticks.

``publish_tick`` collects the engine's per-circuit signed power into a
``TickInputs`` and hands it to ``Emitter.publish_tick``. The emitter does the
rest: BESS dispatch, load shedding, energy integration, panel meter aggregation,
diff publication. /set commands are handled by the emitter's internal default
handlers (no producer-side setter wiring needed)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import aiomqtt
from ebus_emitter import (
    BESSConfig,
    DeviceManifest,
    Emitter,
    LoadSheddingConfig,
    PanelEnvelopeTick,
    SetterRegistry,
    TickInputs,
)

from span_panel_simulator.emitter_adapter.instance_ids import stable_circuit_uuid
from span_panel_simulator.emitter_adapter.spec_generator import build_manifest

if TYPE_CHECKING:
    from span_panel_simulator.engine import DynamicSimulationEngine


@dataclass(slots=True)
class CloneRuntime:
    engine: DynamicSimulationEngine
    manifest: DeviceManifest
    setters: SetterRegistry
    mqtt: Any
    emitter: Emitter
    uuid_to_circuit_id: dict[str, str]


class _AiomqttPublisher:
    """Adapter wrapping ``aiomqtt.Client`` to satisfy the emitter's duck-typed
    MQTT interface (``is_connected``, ``publish``, ``subscribe``)."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        will: aiomqtt.Will | None = None,
        ca_cert_path: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._will = will
        self._ca_cert_path = ca_cert_path
        self._client: aiomqtt.Client | None = None

    async def connect(self) -> None:
        tls_params = (
            aiomqtt.TLSParameters(ca_certs=self._ca_cert_path) if self._ca_cert_path else None
        )
        self._client = aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            identifier=self._client_id,
            username=self._username,
            password=self._password,
            will=self._will,
            tls_params=tls_params,
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


def _config_dict(engine: DynamicSimulationEngine) -> dict[str, Any]:
    """``SimulationConfig`` is a TypedDict that doesn't enumerate optional keys
    (bess/pv/evse/broker). The runtime YAML carries them, so cast for ad-hoc reads."""
    return cast("dict[str, Any]", engine.config)


def _bess_config_from_engine(engine: DynamicSimulationEngine) -> BESSConfig | None:
    """Build the emitter-side BESSConfig from the engine's loaded clone profile.
    Returns None when the profile has no BESS enabled."""
    bess = _config_dict(engine).get("bess") or {}
    if not bess.get("enabled"):
        return None
    raw_mode = bess.get("charge_mode", "self-consumption")
    mode = "backup-only" if raw_mode == "backup-only" else "self-consumption"
    return BESSConfig(
        instance_id=f"{engine.serial_number}-bess",
        nameplate_capacity_kwh=float(bess.get("nameplate_capacity_kwh", 13.5)),
        max_charge_w=float(bess.get("max_charge_w", 3500.0)),
        max_discharge_w=float(bess.get("max_discharge_w", 3500.0)),
        charge_efficiency=float(bess.get("charge_efficiency", 0.95)),
        discharge_efficiency=float(bess.get("discharge_efficiency", 0.95)),
        backup_reserve_pct=float(bess.get("backup_reserve_pct", 20.0)),
        charge_mode=mode,
        charge_hours=tuple(bess.get("charge_hours", [10, 11, 12, 13, 14, 15])),
        discharge_hours=tuple(bess.get("discharge_hours", [17, 18, 19, 20, 21])),
    )


def _load_shedding_config_from_engine(
    engine: DynamicSimulationEngine,
) -> LoadSheddingConfig:
    panel_cfg = _config_dict(engine).get("panel_config", {})
    return LoadSheddingConfig(
        soc_threshold_pct=float(panel_cfg.get("soc_shed_threshold", 20.0)),
    )


async def start_clone(
    engine: DynamicSimulationEngine,
    *,
    broker_host: str | None = None,
    broker_port: int | None = None,
    broker_username: str | None = None,
    broker_password: str | None = None,
    ca_cert_path: str | None = None,
) -> CloneRuntime:
    """Assemble the emitter for ``engine``: build manifest, open MQTT, run
    lifecycle. Returns a runtime the panel holds across ticks.

    Broker connection precedence (highest first):
        1. ``broker:`` section in the YAML config (config explicitness wins).
        2. Explicit ``broker_*`` arguments (typically passed by ``SimulatorApp``).
        3. Default 127.0.0.1:1883 anonymous."""
    cfg = _config_dict(engine)
    manifest = build_manifest(cfg)

    uuid_to_circuit_id = {stable_circuit_uuid(c["id"]): c["id"] for c in engine.config["circuits"]}

    # The emitter registers internal default /set handlers from the empty
    # SetterRegistry — no producer-side wiring required.
    setters = SetterRegistry()

    lwt_topic, lwt_payload, lwt_qos, lwt_retain = Emitter.lwt_settings(manifest)
    will = aiomqtt.Will(
        topic=lwt_topic,
        payload=lwt_payload,
        qos=lwt_qos,
        retain=lwt_retain,
    )

    broker_cfg = cfg.get("broker", {}) or {}
    host = broker_cfg.get("host") or broker_host or "127.0.0.1"
    port_value = broker_cfg.get("port") if broker_cfg.get("port") is not None else broker_port
    port = int(port_value) if port_value is not None else 1883
    username = broker_cfg.get("username") or broker_username
    password = broker_cfg.get("password") or broker_password
    mqtt = _AiomqttPublisher(
        host=host,
        port=int(port),
        client_id=f"span-sim-{engine.serial_number}",
        username=username,
        password=password,
        will=will,
        ca_cert_path=ca_cert_path,
    )
    await mqtt.connect()

    emitter = Emitter(
        manifest,
        setters,
        mqtt,
        bess_config=_bess_config_from_engine(engine),
        load_shedding_config=_load_shedding_config_from_engine(engine),
    )

    runtime = CloneRuntime(
        engine=engine,
        manifest=manifest,
        setters=setters,
        mqtt=mqtt,
        emitter=emitter,
        uuid_to_circuit_id=uuid_to_circuit_id,
    )

    await emitter.start()
    return runtime


async def publish_tick(runtime: CloneRuntime) -> Any:
    """Collect the engine's current per-tick driving signal into ``TickInputs``
    and hand it to the emitter for publication."""
    raw = await runtime.engine.get_tick_inputs()
    tick = TickInputs(
        current_time=raw["current_time"],
        grid_online=raw["grid_online"],
        circuits=raw["circuits"],
        envelope=PanelEnvelopeTick(),
    )
    return await runtime.emitter.publish_tick(tick)


async def stop_clone(runtime: CloneRuntime, *, graceful: bool = True) -> None:
    try:
        await runtime.emitter.stop(graceful=graceful)
    finally:
        await runtime.mqtt.disconnect()
