"""Public surface smoke tests — verify exports are present and Emitter
constructs + publishes against an in-memory FakeMqttClient via publish_tick.

The full publish_tick coverage (BESS, load shedding, /set internal handlers,
seed APIs, etc.) lives in test_publish_tick.py."""

from __future__ import annotations

import pytest

from span_panel_simulator.flat_emitter import (
    BESSConfig,
    BessPhysics,
    CircuitPhysics,
    DeviceInstance,
    DeviceManifest,
    EbusBatterySnapshot,
    EbusCircuitSnapshot,
    EbusEvseSnapshot,
    EbusLugsSnapshot,
    EbusPanelSnapshot,
    EbusPvSnapshot,
    Emitter,
    EmitterError,
    EmitterStateError,
    EvsePhysics,
    Leg,
    LoadSheddingConfig,
    LugsPhysics,
    ManifestPhysicsView,
    ManifestValidationError,
    MissingSetterError,
    PanelEnvelopeTick,
    PanelPhysics,
    PvPhysics,
    RelayRequester,
    RelayResolver,
    RelayState,
    SetterRegistry,
    TickInputs,
    legs_for_tabs,
)


class FakeMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.subscribed: list[str] = []

    def is_connected(self) -> bool:
        return True

    async def publish(
        self,
        topic: str,
        payload: bytes,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        self.published.append((topic, payload, qos, retain))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)


def _manifest() -> DeviceManifest:
    return DeviceManifest(
        instances=(
            DeviceInstance(
                "panel",
                "p1",
                "Span",
                metadata={
                    "vendor-name": "Span",
                    "serial-number": "p1",
                    "firmware-version": "r2026",
                    "hardware-version": "rev2",
                    "panel-size": "32",
                    "main-breaker-rating-a": "200",
                    "panel-model": "MAIN_32",
                    "postal-code": "94103",
                    "time-zone": "America/Los_Angeles",
                },
            ),
            DeviceInstance(
                "circuit",
                "c1",
                "Kitchen",
                metadata={
                    "tab-numbers": "1",
                    "breaker-rating-a": "20",
                    "default-priority": "NICE_TO_HAVE",
                    "relay-behavior": "controllable",
                    "placement": "downstream-of-lugs",
                },
            ),
        )
    )


def test_imports_succeed() -> None:
    """Smoke check that every public name resolves and types are usable."""
    for klass in (
        DeviceInstance,
        DeviceManifest,
        Emitter,
        SetterRegistry,
        BESSConfig,
        LoadSheddingConfig,
        TickInputs,
        PanelEnvelopeTick,
        ManifestPhysicsView,
        PanelPhysics,
        CircuitPhysics,
        BessPhysics,
        PvPhysics,
        EvsePhysics,
        LugsPhysics,
        RelayResolver,
        RelayState,
        RelayRequester,
        Leg,
    ):
        assert callable(klass) or isinstance(klass, type)
    assert callable(legs_for_tabs)
    for snap_cls in (
        EbusPanelSnapshot,
        EbusCircuitSnapshot,
        EbusBatterySnapshot,
        EbusPvSnapshot,
        EbusEvseSnapshot,
        EbusLugsSnapshot,
    ):
        assert callable(snap_cls)
    for exc in (EmitterError, EmitterStateError, ManifestValidationError, MissingSetterError):
        assert issubclass(exc, Exception)


def test_emitter_init_fills_in_default_setter_handlers() -> None:
    """v0.3.0 contract: emitter registers internal default handlers for the
    four settable properties when the producer hasn't supplied one. An empty
    SetterRegistry no longer triggers MissingSetterError."""
    setters = SetterRegistry()
    Emitter(_manifest(), setters, FakeMqttClient())
    assert setters.get("circuit", "circuit/relay") is not None
    assert setters.get("circuit", "circuit/shed-priority") is not None
    assert setters.get("circuit", "circuit/name") is not None
    assert setters.get("panel", "core/dominant-power-source") is not None


@pytest.mark.asyncio
async def test_emitter_lifecycle_start_publish_stop() -> None:
    mqtt = FakeMqttClient()
    emitter = Emitter(_manifest(), SetterRegistry(), mqtt)
    await emitter.start()
    snapshot = await emitter.publish_tick(
        TickInputs(current_time=0.0, grid_online=True, circuits={"c1": 200.0}),
    )
    assert snapshot.info.serial_number == "p1"
    assert any(t.endswith("$state") for (t, _, _, _) in mqtt.published)
    assert emitter.last_snapshot is snapshot
    await emitter.stop(graceful=True)


@pytest.mark.asyncio
async def test_publish_tick_before_start_raises() -> None:
    emitter = Emitter(_manifest(), SetterRegistry(), FakeMqttClient())
    with pytest.raises(EmitterStateError):
        await emitter.publish_tick(
            TickInputs(current_time=0.0, grid_online=True, circuits={"c1": 0.0}),
        )
