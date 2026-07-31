"""Tests for PanelInstance lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from span_panel_simulator.panel import PanelInstance


def _write_simple_config(tmp_path: Path, broker_host: str, broker_port: int) -> Path:
    config = tmp_path / "test_panel.yaml"
    config.write_text(f"""\
panel_config:
  serial_number: "SIM-PANEL-A"
  total_tabs: 8
  main_size: 100

broker:
  host: "{broker_host}"
  port: {broker_port}

circuit_templates:
  lighting:
    energy_profile:
      mode: "consumer"
      power_range: [5.0, 50.0]
      typical_power: 25.0
      power_variation: 0.1
    relay_behavior: "controllable"
    priority: "NEVER"

circuits:
  - id: "test_circuit"
    name: "Test Circuit"
    template: "lighting"
    tabs: [1]

unmapped_tabs: []

simulation_params:
  update_interval: 5
  time_acceleration: 1.0
  noise_factor: 0.0
  enable_realistic_behaviors: false
""")
    return config


@pytest.fixture
def simple_config(tmp_path: Path) -> Path:
    """Default config with a non-routable broker placeholder. Tests requiring a live
    broker should use ``simple_config_with_broker`` (see tests/integration/conftest.py
    for the ``amqtt_broker`` fixture)."""
    return _write_simple_config(tmp_path, "127.0.0.1", 1)


@pytest.fixture
def simple_config_with_broker(tmp_path: Path, amqtt_broker: tuple[str, int]) -> Path:
    """Config wired to the in-process amqtt broker fixture."""
    host, port = amqtt_broker
    return _write_simple_config(tmp_path, host, port)


class TestPanelInstance:
    """PanelInstance start/stop/reload lifecycle."""

    @pytest.mark.asyncio
    async def test_start_and_stop(self, simple_config_with_broker: Path) -> None:
        panel = PanelInstance(simple_config_with_broker)
        serial = await panel.start()
        assert serial == "SIM-PANEL-A"
        assert panel.is_running
        assert panel.runtime is not None
        await panel.stop()
        assert not panel.is_running

    @pytest.mark.asyncio
    async def test_reload_restarts(self, simple_config_with_broker: Path) -> None:
        panel = PanelInstance(simple_config_with_broker)
        await panel.start()
        assert panel.is_running
        serial = await panel.reload()
        assert serial == "SIM-PANEL-A"
        assert panel.is_running
        await panel.stop()

    @pytest.mark.asyncio
    async def test_serial_before_start_raises(self, simple_config: Path) -> None:
        panel = PanelInstance(simple_config)
        with pytest.raises(RuntimeError, match="not initialised"):
            _ = panel.serial_number


# Engine-only tests removed post-cutover — the legacy DynamicSimulationEngine and its
# in-memory snapshot/total_tabs accessors were lifted into
# span_panel_simulator.flat_emitter.scheduleRunner.
# Equivalent behaviour is exercised by:
#   - tests/emitter_adapter/test_spec_generator.py (manifest + runtime spec construction)
#   - the emitter package's own scheduleRunner test suite
#   - tests/integration/test_panel_emits.py (broker-driven end-to-end)
