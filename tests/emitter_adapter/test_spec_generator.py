from pathlib import Path

import pytest
import yaml

from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.spec_generator import build_manifest


def _profile() -> dict:
    """Load the default_MAIN_40 clone profile fixture."""
    return yaml.safe_load(Path("configs/default_MAIN_40.yaml").read_text())


def test_build_manifest_includes_panel_lugs_and_circuits() -> None:
    manifest = build_manifest(_profile())
    assert len(manifest.of_class("panel")) == 1
    assert len(manifest.of_class("lugs")) == 2
    assert len(manifest.of_class("circuit")) > 0


def test_build_manifest_includes_bess_when_enabled() -> None:
    profile = _profile()
    if profile.get("bess", {}).get("enabled"):
        manifest = build_manifest(profile)
        assert len(manifest.of_class("bess")) == 1
    else:
        pytest.skip("default_MAIN_40 has no enabled BESS")


def test_build_manifest_derives_pv_and_evse_from_device_type_templates() -> None:
    manifest = build_manifest(_profile())

    pv = manifest.of_class("pv")[0]
    assert pv.instance_id == "sim-40t-001-pv-1"
    assert pv.metadata["feed"] == stable_circuit_uuid("solar_inverter")
    assert pv.metadata["relative-position"] == "IN_PANEL"

    evse = manifest.of_class("evse")[0]
    assert evse.instance_id == "sim-40t-001-sim-evse-sim-40t-001"
    assert evse.metadata["feed"] == stable_circuit_uuid("span_drive_garage")
    assert len(manifest.of_class("evse")) == 2
    assert manifest.of_class("evse")[1].instance_id == "sim-40t-001-sim-evse-sim-40t-001-2"
    assert manifest.of_class("evse")[1].metadata["feed"] == stable_circuit_uuid(
        "span_drive_driveway",
    )


def test_build_manifest_omits_native_devices_when_disabled() -> None:
    profile = {
        "panel_config": {"serial_number": "test-001"},
        "circuits": [],
    }
    manifest = build_manifest(profile)
    assert len(manifest.of_class("bess")) == 0
    assert len(manifest.of_class("pv")) == 0
    assert len(manifest.of_class("evse")) == 0


def test_build_manifest_panel_id_matches_serial() -> None:
    profile = {
        "panel_config": {"serial_number": "abc-123", "display_name": "Test Panel"},
        "circuits": [],
    }
    manifest = build_manifest(profile)
    panel = manifest.of_class("panel")[0]
    assert panel.instance_id == "abc-123"
    assert panel.display_name == "Test Panel"


# ---- v0.3.0 physics-key emission --------------------------------------------


def test_panel_metadata_includes_physics_keys() -> None:
    profile = {
        "panel_config": {
            "serial_number": "abc-123",
            "total_tabs": 40,
            "main_size": 200,
            "postal_code": "94110",
            "time_zone": "America/Los_Angeles",
        },
        "circuits": [],
    }
    panel = build_manifest(profile).of_class("panel")[0]
    assert panel.metadata["panel-size"] == "40"
    assert panel.metadata["main-breaker-rating-a"] == "200"
    assert panel.metadata["panel-model"] == "MAIN_40"
    assert panel.metadata["postal-code"] == "94110"
    assert panel.metadata["time-zone"] == "America/Los_Angeles"
    assert panel.metadata["service-voltage-v"] == "240.0"
    assert panel.metadata["line-voltage-v"] == "120.0"
    assert panel.metadata["islandable"] == "false"


def test_a_circuit_may_still_declare_itself_downstream_of_the_lugs() -> None:
    """The default is `upstream-of-lugs` because that is where a main panel's
    circuits sit, but the emitter accepts either value and a config that means the
    other one must be able to say so. Keeping the override is what stops the
    default from being a silent policy: `test_lugs_are_distinguishable.py` then
    catches the case where *everything* ends up downstream.
    """
    profile = {
        "panel_config": {"serial_number": "abc-123", "total_tabs": 40, "main_size": 200},
        "circuits": [
            {"id": "shop", "name": "Shop Feed", "tabs": [5], "placement": "downstream-of-lugs"},
            {"id": "kitchen", "name": "Kitchen", "tabs": [1]},
        ],
    }
    by_name = {c.display_name: c for c in build_manifest(profile).of_class("circuit")}

    assert by_name["Shop Feed"].metadata["placement"] == "downstream-of-lugs"
    assert by_name["Kitchen"].metadata["placement"] == "upstream-of-lugs"


def test_circuit_metadata_includes_physics_keys() -> None:
    profile = {
        "panel_config": {"serial_number": "abc-123", "total_tabs": 40, "main_size": 200},
        "circuit_templates": {
            "lighting": {
                "priority": "NICE_TO_HAVE",
                "relay_behavior": "controllable",
                "breaker_rating_a": 15.0,
            },
        },
        "circuits": [
            {"id": "kitchen", "name": "Kitchen", "template": "lighting", "tabs": [1]},
            {"id": "hvac", "name": "HVAC", "template": "lighting", "tabs": [3, 4]},
        ],
    }
    manifest = build_manifest(profile)
    circuits = manifest.of_class("circuit")
    assert len(circuits) == 2

    by_name = {c.display_name: c for c in circuits}
    kitchen = by_name["Kitchen"]
    assert kitchen.metadata["tab-numbers"] == "1"
    assert kitchen.metadata["breaker-rating-a"] == "15.0"
    assert kitchen.metadata["default-priority"] == "NICE_TO_HAVE"
    assert kitchen.metadata["relay-behavior"] == "controllable"
    assert kitchen.metadata["placement"] == "upstream-of-lugs"
    assert kitchen.metadata["always-on"] == "false"

    hvac = by_name["HVAC"]
    assert hvac.metadata["tab-numbers"] == "3,4"


def test_bess_metadata_includes_physics_keys() -> None:
    profile = {
        "panel_config": {"serial_number": "abc-123", "total_tabs": 40, "main_size": 200},
        "circuits": [],
        "bess": {"enabled": True, "nameplate_capacity_kwh": 13.5, "initial_soe_kwh": 6.75},
    }
    bess = build_manifest(profile).of_class("bess")[0]
    assert bess.instance_id == "abc-123-bess"
    assert bess.metadata["vendor-name"] == "Span"
    assert bess.metadata["nameplate-capacity-kwh"] == "13.5"
    assert bess.metadata["relative-position"] == "UPSTREAM"
    assert bess.metadata["initial-soe-kwh"] == "6.75"


def test_pv_metadata_includes_inverter_type() -> None:
    profile = {
        "panel_config": {"serial_number": "abc-123", "total_tabs": 40, "main_size": 200},
        "circuits": [],
        "pv": {
            "enabled": True,
            "vendor": "Enphase",
            "nameplate_capacity_w": 7000.0,
            "inverter_type": "hybrid",
        },
    }
    manifest = build_manifest(profile)
    pv = manifest.of_class("pv")[0]
    assert pv.metadata["inverter-type"] == "hybrid"
    # `nominal-power-w`, not `nameplate-capacity-w`: the emitter requires this
    # spelling and rejects the manifest without it.
    assert pv.metadata["nominal-power-w"] == "7000.0"
    assert pv.metadata["relative-position"] == "UPSTREAM"
    # Hybrid PV → panel becomes islandable.
    panel = manifest.of_class("panel")[0]
    assert panel.metadata["islandable"] == "true"


def test_evse_metadata_includes_physics_keys() -> None:
    profile = {
        "panel_config": {"serial_number": "abc-123", "total_tabs": 40, "main_size": 200},
        "circuits": [],
        "evse": {"enabled": True, "max_current_a": 40.0},
    }
    evse = build_manifest(profile).of_class("evse")[0]
    assert evse.metadata["max-current-a"] == "40.0"
    # v1.0 split the SKU from the human designation: `part-number` is the SKU,
    # `model` the designation. This used to publish the designation as
    # `product-name`, which the emitter does not read and consumers never saw.
    assert evse.metadata["model"] == "SPAN Drive"
    assert evse.metadata["part-number"] == "SPN-DRV-001"


def test_circuit_relay_behavior_translates_underscore_to_hyphen() -> None:
    profile = {
        "panel_config": {"serial_number": "abc-123", "total_tabs": 40, "main_size": 200},
        "circuit_templates": {
            "always": {"priority": "MUST_HAVE", "relay_behavior": "always_on"},
        },
        "circuits": [{"id": "smoke", "name": "Smoke Alarm", "template": "always", "tabs": [1]}],
    }
    c = build_manifest(profile).of_class("circuit")[0]
    assert c.metadata["relay-behavior"] == "always-on"
    assert c.metadata["always-on"] == "true"
