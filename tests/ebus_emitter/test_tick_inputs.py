from span_panel_simulator.flat_emitter.tick_inputs import PanelEnvelopeTick, TickInputs


def test_tick_inputs_defaults() -> None:
    t = TickInputs(current_time=100.0, grid_online=True, circuits={"c1": 500.0})
    assert t.current_time == 100.0
    assert t.grid_online is True
    assert t.circuits == {"c1": 500.0}
    assert t.evse == {}
    assert isinstance(t.envelope, PanelEnvelopeTick)
    assert t.envelope.door_state == "CLOSED"


def test_panel_envelope_tick_defaults() -> None:
    e = PanelEnvelopeTick()
    assert e.door_state == "CLOSED"
    assert e.proximity_proven is True
    assert e.eth0_link is True
    assert e.wlan_link is True
    assert e.wwan_link is False
    assert e.uptime_s == 0
    assert e.wifi_ssid is None
    assert e.cloud_connection == "CONNECTED"


def test_tick_inputs_is_mutable() -> None:
    t = TickInputs(current_time=0.0, grid_online=True, circuits={})
    t.circuits["c1"] = 500.0
    t.evse["ev1"] = 7000.0
    assert t.circuits == {"c1": 500.0}
    assert t.evse == {"ev1": 7000.0}


def test_envelope_overrides_apply() -> None:
    env = PanelEnvelopeTick(
        door_state="OPEN",
        proximity_proven=False,
        wifi_ssid="MyHouse",
    )
    t = TickInputs(current_time=0.0, grid_online=True, circuits={}, envelope=env)
    assert t.envelope.door_state == "OPEN"
    assert t.envelope.proximity_proven is False
    assert t.envelope.wifi_ssid == "MyHouse"
