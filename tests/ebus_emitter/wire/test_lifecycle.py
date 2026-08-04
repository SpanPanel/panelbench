import pytest

from span_panel_simulator.ebus_emitter.manifest import DeviceInstance, DeviceManifest
from span_panel_simulator.ebus_emitter.wire.graph_builder import build_graph
from span_panel_simulator.ebus_emitter.wire.lifecycle import LifecycleController, lwt_settings
from span_panel_simulator.ebus_emitter.wire.mapping_loader import load_mapping_table
from span_panel_simulator.ebus_emitter.wire.profile_loader import load_profiles


class FakeMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.subscribed: list[str] = []
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

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
            DeviceInstance("panel", "p1", "Span"),
            DeviceInstance("circuit", "c1", "Kitchen"),
        )
    )


def _build(mqtt: FakeMqttClient) -> LifecycleController:
    profiles = load_profiles()
    mapping = load_mapping_table()
    graph = build_graph(_manifest(), mapping, profiles)
    return LifecycleController(
        _manifest(),
        mapping,
        profiles,
        graph,
        mqtt,
        domain="ebus",
        bus_version="5",
        subscriptions=[],
    )


@pytest.mark.asyncio
async def test_v1_cold_start_publishes_init_description_ready_in_order() -> None:
    mqtt = FakeMqttClient()
    lc = _build(mqtt)
    await lc.start()

    # First publish must be $state=init on the root.
    assert mqtt.published[0] == ("ebus/5/p1/$state", b"init", 1, True)
    # Last publish must be $state=ready on the root.
    assert mqtt.published[-1] == ("ebus/5/p1/$state", b"ready", 1, True)
    # At least one $description publish in between.
    description_topics = [t for (t, _, _, _) in mqtt.published if t.endswith("$description")]
    assert "ebus/5/p1/$description" in description_topics


def test_lwt_settings_returns_root_state_topic_with_lost_payload() -> None:
    topic, payload, qos, retain = lwt_settings(
        _manifest(),
        domain="ebus",
        bus_version="5",
        root_entity_class="panel",
    )
    assert topic == "ebus/5/p1/$state"
    assert payload == b"lost"
    assert qos == 1
    assert retain is True


@pytest.mark.asyncio
async def test_graceful_stop_publishes_disconnected_on_root() -> None:
    mqtt = FakeMqttClient()
    lc = _build(mqtt)
    await lc.start()
    mqtt.published.clear()
    await lc.stop(graceful=True)
    assert ("ebus/5/p1/$state", b"disconnected", 1, True) in mqtt.published


@pytest.mark.asyncio
async def test_graceful_stop_can_clear_retained_topics() -> None:
    mqtt = FakeMqttClient()
    lc = _build(mqtt)
    await lc.start()
    mqtt.published.clear()
    await lc.stop(graceful=True, clear_retained=True)

    tombstones = {
        topic for topic, payload, _qos, retain in mqtt.published if payload == b"" and retain
    }
    assert "ebus/5/p1/$state" in tombstones
    assert "ebus/5/p1/$description" in tombstones
    # A circuit is its own device now, so its topics hang off the circuit id and a bare
    # capability node — not off the panel with a circuit-namespaced node.
    assert "ebus/5/c1/$state" in tombstones
    assert "ebus/5/c1/$description" in tombstones
    assert "ebus/5/c1/meter/active-power" in tombstones


@pytest.mark.asyncio
async def test_non_graceful_stop_publishes_nothing() -> None:
    mqtt = FakeMqttClient()
    lc = _build(mqtt)
    await lc.start()
    mqtt.published.clear()
    await lc.stop(graceful=False)
    assert mqtt.published == []
