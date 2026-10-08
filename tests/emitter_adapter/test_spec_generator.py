from pathlib import Path

import pytest
import yaml

from panelbench.config_types import (
    BESSConfigYAML,
    CircuitDefinitionExtended,
    CircuitTemplateExtended,
    SimulationConfig,
)
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.spec_generator import build_manifest
from tests._helpers import default_config

_SERIAL = "sim-40t-001"
"""The serial `default_MAIN_40.yaml` declares.

Named because circuit device ids are scoped to their panel, so it is now an input
to a feed id and not only the prefix of the proxied ids below.
"""


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
    assert pv.metadata["feed"] == stable_circuit_uuid(_SERIAL, "solar_inverter")
    assert pv.metadata["relative-position"] == "IN_PANEL"

    evse = manifest.of_class("evse")[0]
    assert evse.instance_id == "sim-40t-001-sim-evse-sim-40t-001"
    assert evse.metadata["feed"] == stable_circuit_uuid(_SERIAL, "span_drive_garage")
    assert len(manifest.of_class("evse")) == 2
    assert manifest.of_class("evse")[1].instance_id == "sim-40t-001-sim-evse-sim-40t-001-2"
    assert manifest.of_class("evse")[1].metadata["feed"] == stable_circuit_uuid(
        _SERIAL,
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
    # A hybrid inverter used to make the panel islandable on its own. It no longer
    # does, and this profile is why the inference was wrong: there is no BESS here, so
    # there is no grid-forming source to island with. Islandability follows the
    # battery now, and the assertion is kept rather than deleted because "hybrid PV
    # alone is not an island" is the specific claim that changed.
    panel = manifest.of_class("panel")[0]
    assert panel.metadata["islandable"] == "false"


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


def _template() -> CircuitTemplateExtended:
    return {
        "energy_profile": {
            "mode": "consumer",
            "power_range": [0.0, 100.0],
            "typical_power": 10.0,
            "power_variation": 0.1,
        },
        "relay_behavior": "controllable",
        "priority": "NEVER",
    }


def _circuit() -> CircuitDefinitionExtended:
    return {"id": "c", "name": "C", "template": "t", "tabs": [1]}


def _one_circuit_profile(
    template: CircuitTemplateExtended | None = None,
    circuit: CircuitDefinitionExtended | None = None,
) -> SimulationConfig:
    """The shipped MAIN 40, reduced to one circuit and no battery, to change in place."""
    config = default_config()
    config["panel_config"]["serial_number"] = "abc-123"
    config["circuit_templates"] = {"t": template or _template()}
    config["circuits"] = [circuit or _circuit()]
    config["bess"] = None
    config["pv"] = None
    config["evse"] = None
    return config


def _battery_profile(bess: BESSConfigYAML) -> SimulationConfig:
    """The reduced panel with *bess*, a commissioned battery, so grid-forming with a MID."""
    config = _one_circuit_profile()
    config["bess"] = {"enabled": True, "nameplate_capacity_kwh": 13.5, **bess}
    return config


def test_a_rating_recorded_as_absent_reaches_the_emitter_as_the_placeholder() -> None:
    """The emitter requires a rating, so a clone's absent one is given its documented
    placeholder rather than refused; the fidelity test pins what that publishes."""
    template = _template()
    template["breaker_rating"] = None

    [circuit] = build_manifest(_one_circuit_profile(template)).of_class("circuit")

    assert circuit.metadata["breaker-rating-a"] == "20.0"


def test_a_pcs_priority_recorded_as_absent_gives_the_emitter_none() -> None:
    unpublished, named = _circuit(), _circuit()
    unpublished["pcs_priority"] = None
    named["pcs_priority"] = 7

    [absent] = build_manifest(_one_circuit_profile(circuit=unpublished)).of_class("circuit")
    [given] = build_manifest(_one_circuit_profile(circuit=named)).of_class("circuit")
    [positional] = build_manifest(_one_circuit_profile()).of_class("circuit")

    assert "pcs-priority" not in absent.metadata
    assert given.metadata["pcs-priority"] == "7"
    assert positional.metadata["pcs-priority"] == "1"


def test_the_panel_publishes_the_vendor_its_config_names() -> None:
    profile = _one_circuit_profile()
    profile["panel_config"]["vendor_name"] = "SPAN"

    assert build_manifest(profile).of_class("panel")[0].metadata["vendor-name"] == "SPAN"


def test_the_panel_publishes_the_model_its_config_names() -> None:
    """Verbatim, where it was re-derived from the size."""
    profile = _one_circuit_profile()
    profile["panel_config"]["model"] = "MAIN_32"

    assert build_manifest(profile).of_class("panel")[0].metadata["panel-model"] == "MAIN_32"


def test_a_mid_publishes_its_own_serial_where_the_config_names_one() -> None:
    profile = _battery_profile(
        {"serial_number": "example-bess-0001", "mid_serial_number": "example-mid-0001"}
    )

    [mid] = build_manifest(profile).of_class("mid")

    assert mid.metadata["serial-number"] == "example-mid-0001"


def test_a_mid_publishes_its_own_vendor_where_the_config_names_one() -> None:
    profile = _battery_profile(
        {"vendor": "Example Battery Co", "mid_vendor": "Example Gateway Co"}
    )

    [mid] = build_manifest(profile).of_class("mid")

    assert mid.metadata["vendor-name"] == "Example Gateway Co"


def test_a_clones_energy_seeds_reach_the_emitter() -> None:
    """Without them a clone's energy registers start at zero, whatever the panel's read."""
    template = _template()
    template["energy_profile"]["initial_consumed_energy_wh"] = 129126.5
    template["energy_profile"]["initial_produced_energy_wh"] = 11355.5

    [circuit] = build_manifest(_one_circuit_profile(template)).of_class("circuit")

    assert circuit.metadata["initial-consumed-wh"] == "129126.5"
    assert circuit.metadata["initial-produced-wh"] == "11355.5"


def test_the_battery_mid_and_lugs_are_named_after_their_ids() -> None:
    """As SPAN release 202639 names them on the wire (the captured MAIN 32 does), where
    PanelBench named them "Battery", "Microgrid Interconnect Device" and "Upstream lugs"."""
    manifest = build_manifest(_battery_profile({}))

    devices = [*manifest.of_class("bess"), *manifest.of_class("mid"), *manifest.of_class("lugs")]

    assert len(devices) == 4, "the profile names no battery and MID, so the test proves less"
    assert all(device.display_name == device.instance_id for device in devices)
