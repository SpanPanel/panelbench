from pathlib import Path

import pytest

from span_panel_simulator.ebus_emitter.exceptions import ProfileValidationError
from span_panel_simulator.ebus_emitter.wire.mapping_loader import MappingTable, load_mapping_table
from span_panel_simulator.ebus_emitter.wire.profile_loader import ProfileTable, load_profiles


def test_load_profiles_returns_all_vendored() -> None:
    profiles = load_profiles()
    assert isinstance(profiles, ProfileTable)
    for cls in ("panel", "circuit", "lugs", "bess", "pv", "evse"):
        assert cls in profiles, f"profile {cls} missing"


def test_load_mapping_returns_all_vendored() -> None:
    mapping = load_mapping_table()
    assert isinstance(mapping, MappingTable)
    for cls in ("panel", "circuit", "lugs", "bess", "pv", "evse"):
        assert cls in mapping, f"mapping {cls} missing"


def test_mapping_cross_check_passes_against_vendored_profiles() -> None:
    profiles = load_profiles()
    mapping = load_mapping_table()
    mapping.validate_against(profiles)


def test_mapping_cross_check_rejects_unknown_parent_entity_class(tmp_path: Path) -> None:
    panel_yaml = """\
entity_class: panel
profile: panel.json
profile_version: 1
placement:
  kind: root-device
  device_id_template: "{instance_id}"
wire:
  device_id_source: self
  property_path_template: "{capability}/{property_key}"
display:
  name_template: "{display_name}"
  fallback_name_template: "Panel {instance_id_short}"
discovery:
  $description_owner: self
  state_owner: self
"""
    bad_yaml = """\
entity_class: circuit
profile: circuit.json
profile_version: 1
placement:
  kind: node-on-parent
  parent_entity_class: nonexistent
  node_id_template: "{instance_id}"
wire:
  device_id_source: parent
  property_path_template: "{node_id}/{property_key}"
display:
  name_template: "{display_name}"
  fallback_name_template: "Circuit {instance_id_short}"
discovery:
  $description_owner: parent
  state_owner: parent
"""
    (tmp_path / "panel.yaml").write_text(panel_yaml)
    (tmp_path / "circuit.yaml").write_text(bad_yaml)
    profiles = load_profiles()
    mapping = load_mapping_table(directory=tmp_path)
    with pytest.raises(ProfileValidationError, match="parent_entity_class"):
        mapping.validate_against(profiles)


def test_settable_properties_circuit_includes_relay_and_priority() -> None:
    profiles = load_profiles()
    settables = profiles["circuit"].settable_properties()
    assert ("circuit", "relay") in settables
    assert ("circuit", "shed-priority") in settables


def test_settable_properties_panel_includes_dominant_power_source() -> None:
    profiles = load_profiles()
    settables = profiles["panel"].settable_properties()
    assert ("core", "dominant-power-source") in settables
