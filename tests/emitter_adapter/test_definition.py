"""`build_definition` is PanelBench's single statement of what a panel *is*."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from ebus_panel_sim import LoadSheddingConfig

from panelbench.emitter_adapter.definition import (
    bess_config,
    build_definition,
    load_shedding_config,
)
from panelbench.emitter_adapter.instance_ids import bess_device_id
from panelbench.emitter_adapter.spec_generator import build_manifest
from tests._helpers import default_config

if TYPE_CHECKING:
    from panelbench.config_types import BESSConfigYAML


def test_the_definition_carries_the_manifest_battery_and_shed_policy() -> None:
    config = default_config()
    serial = config["panel_config"]["serial_number"]

    definition = build_definition(config)

    assert definition.manifest == build_manifest(config)
    assert definition.variant == "span"
    assert [b.instance_id for b in definition.bess_configs] == [
        bess_device_id(serial, config["bess"])
    ]
    assert definition.load_shedding == load_shedding_config(config["panel_config"])


def test_a_panel_without_a_battery_has_no_native_battery() -> None:
    config = default_config()
    config["bess"] = {"enabled": False}

    assert build_definition(config).bess_configs == ()


def test_the_shed_threshold_defaults_to_twenty_percent() -> None:
    panel = default_config()["panel_config"]
    panel.pop("soc_shed_threshold", None)

    assert load_shedding_config(panel) == LoadSheddingConfig(soc_threshold_pct=20.0)


def test_a_disabled_battery_builds_no_config() -> None:
    assert bess_config("sim-x", {"enabled": False}) is None


@pytest.mark.parametrize(("bess", "soc"), [({"initial_soe_kwh": 2.5}, 25.0), ({}, 50.0)])
def test_the_starting_charge_is_initial_soe_kwh(bess: BESSConfigYAML, soc: float) -> None:
    """One fact, one key: the emitter's percentage comes from the YAML's kWh."""
    battery = bess_config("sim-x", {"enabled": True, "nameplate_capacity_kwh": 10.0, **bess})

    assert battery is not None
    assert battery.initial_soc_pct == soc
