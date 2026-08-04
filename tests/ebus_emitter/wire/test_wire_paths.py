from span_panel_simulator.ebus_emitter.wire.wire_paths import (
    device_description_topic,
    device_state_topic,
    parse_set_topic,
    root_state_topic,
    set_topic_for,
)


def test_root_state_topic() -> None:
    assert root_state_topic("ebus", "5", "panel-1") == "ebus/5/panel-1/$state"


def test_device_state_and_description() -> None:
    assert device_state_topic("ebus", "5", "panel-1") == "ebus/5/panel-1/$state"
    assert device_description_topic("ebus", "5", "panel-1") == "ebus/5/panel-1/$description"


def test_set_topic_for() -> None:
    assert (
        set_topic_for("ebus", "5", "panel-1", "switch", "relay")
        == "ebus/5/panel-1/switch/relay/set"
    )


def test_parse_set_topic_matches_expected_shape() -> None:
    parsed = parse_set_topic("ebus/5/panel-1/switch/relay/set", "ebus", "5")
    assert parsed == ("panel-1", "switch", "relay")


def test_parse_set_topic_returns_none_on_mismatch() -> None:
    assert parse_set_topic("nope", "ebus", "5") is None
    assert parse_set_topic("ebus/5/panel-1/$state", "ebus", "5") is None
    assert parse_set_topic("ebus/5/panel-1/x", "ebus", "5") is None
