"""Unit tests for the runtime helpers that don't require a real broker.
End-to-end start_clone/publish_tick/stop_clone is exercised by the test_panel
integration tests against the in-process amqtt broker fixture."""

from unittest.mock import MagicMock

from span_panel_simulator.emitter_adapter.runtime import (
    _bess_config_from_engine,
    _load_shedding_config_from_engine,
)


def test_bess_config_from_engine_returns_none_when_disabled() -> None:
    engine = MagicMock()
    engine.config = {"panel_config": {"serial_number": "x"}, "bess": {"enabled": False}}
    assert _bess_config_from_engine(engine) is None


def test_bess_config_from_engine_returns_none_when_missing() -> None:
    engine = MagicMock()
    engine.config = {"panel_config": {"serial_number": "x"}}
    assert _bess_config_from_engine(engine) is None


def test_bess_config_from_engine_uses_yaml_values() -> None:
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
    cfg = _bess_config_from_engine(engine)
    assert cfg is not None
    assert cfg.instance_id == "abc-bess"
    assert cfg.nameplate_capacity_kwh == 20.0
    assert cfg.max_charge_w == 5000.0
    assert cfg.charge_mode == "backup-only"
    assert cfg.charge_hours == (9, 10, 11)
    assert cfg.discharge_hours == (18, 19)


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
