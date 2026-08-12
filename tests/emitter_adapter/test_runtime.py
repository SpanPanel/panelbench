"""Unit tests for the runtime helpers that don't require a real broker.
End-to-end start_clone/publish_tick/stop_clone is exercised by the test_panel
integration tests against the in-process amqtt broker fixture."""

from unittest.mock import MagicMock

from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.runtime import (
    _evse_tick_inputs,
    _load_shedding_config_from_engine,
    bess_config_from_engine,
)


def testbess_config_from_engine_returns_none_when_disabled() -> None:
    engine = MagicMock()
    engine.config = {"panel_config": {"serial_number": "x"}, "bess": {"enabled": False}}
    assert bess_config_from_engine(engine) is None


def testbess_config_from_engine_returns_none_when_missing() -> None:
    engine = MagicMock()
    engine.config = {"panel_config": {"serial_number": "x"}}
    assert bess_config_from_engine(engine) is None


def testbess_config_from_engine_uses_yaml_values() -> None:
    engine = MagicMock()
    engine.serial_number = "abc"
    engine.config = {
        "panel_config": {"serial_number": "abc"},
        "bess": {
            "enabled": True,
            "nameplate_capacity_kwh": 20.0,
            "max_charge_w": 5000.0,
            "max_discharge_w": 6000.0,
            "charge_efficiency": 0.92,
            "discharge_efficiency": 0.93,
            "backup_reserve_pct": 30.0,
            "charge_mode": "backup-only",
            "charge_hours": [9, 10, 11],
            "discharge_hours": [18, 19],
        },
    }
    cfg = bess_config_from_engine(engine)
    assert cfg is not None
    assert cfg.instance_id == "abc-bess"
    assert cfg.nameplate_capacity_kwh == 20.0
    assert cfg.max_charge_w == 5000.0
    assert cfg.charge_mode == "backup-only"
    assert cfg.charge_hours == (9, 10, 11)
    assert cfg.discharge_hours == (18, 19)


def testbess_config_from_engine_uses_explicit_instance_id() -> None:
    engine = MagicMock()
    engine.serial_number = "abc"
    engine.config = {
        "panel_config": {"serial_number": "abc"},
        "bess": {
            "enabled": True,
            "instance_id": "bess-0",
            "nameplate_capacity_kwh": 20.0,
            "max_charge_w": 5000.0,
            "max_discharge_w": 6000.0,
        },
    }
    cfg = bess_config_from_engine(engine)
    assert cfg is not None
    assert cfg.instance_id == "abc-bess-0"


def test_load_shedding_config_default_threshold() -> None:
    engine = MagicMock()
    engine.config = {"panel_config": {"serial_number": "x"}}
    cfg = _load_shedding_config_from_engine(engine)
    assert cfg.soc_threshold_pct == 20.0


def test_load_shedding_config_custom_threshold() -> None:
    engine = MagicMock()
    engine.config = {"panel_config": {"serial_number": "x", "soc_shed_threshold": 35.0}}
    cfg = _load_shedding_config_from_engine(engine)
    assert cfg.soc_threshold_pct == 35.0


def test_evse_tick_inputs_include_each_evse_feed() -> None:
    config = {
        "panel_config": {"serial_number": "abc"},
        "circuit_templates": {
            "span_drive": {"device_type": "evse"},
            "lighting": {},
        },
        "circuits": [
            {"id": "span_drive_garage", "template": "span_drive"},
            {"id": "span_drive_driveway", "template": "span_drive"},
            {"id": "kitchen", "template": "lighting"},
        ],
    }
    circuit_powers = {
        stable_circuit_uuid("span_drive_garage"): 7200.0,
        stable_circuit_uuid("span_drive_driveway"): 3600.0,
    }
    # Keys must equal the manifest's EVSE device ids: the emitter looks its EVSE
    # physics up by instance id, so a disagreement here is a KeyError, not a
    # cosmetic drift. Both sides derive them from `instance_ids`.
    assert _evse_tick_inputs(config, circuit_powers) == {
        "abc-sim-evse-abc": 7200.0,
        "abc-sim-evse-abc-2": 3600.0,
    }
