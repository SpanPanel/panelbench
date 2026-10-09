"""What a panel records at commissioning reaches the wire, and a clone reads it back.

A config states each fact once (``emitter_adapter.commissioning``); the emitter
publishes it verbatim where the config's variant declares the property; and a clone
of that panel writes the same fact into its own config. So a config, published and
cloned, keeps every fact it stated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.clone import translate_panel_tree
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from panelbench.validation import validate_yaml_config
from tests._helpers import default_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.config_types import LugsSectionYAML, SimulationConfig, SiteConfigYAML

_HARDWARE = "3.0"
"""A hardware version string that selects the variant declaring every fact here."""
_SITE: SiteConfigYAML = {
    "name": "Example Site",
    "address_lines": "1 Example Street",
    "locality": "Example City",
    "region": "Example Region",
    "country_code": "US",
    "utility_meter_serial_number": "EXAMPLE-METER",
}
_LUGS: LugsSectionYAML = {
    "upstream": {"service_rating_a": 200, "overcurrent_protection_a": 200, "feeds_role": "LOADS"},
    "downstream": {
        "feeds_role": "UNUSED",
        "backed_up": "NOT_BACKED_UP",
        "unvalued": ["meter/active-power", "meter/exported-energy", "meter/imported-energy"],
    },
}
_TAGS = ["FRIDGE", "FREEZER"]
_LOCATIONS = ["KITCHEN"]
_PROTECTION = "OVERCURRENT"


def _commissioned() -> SimulationConfig:
    """The shipped template on hardware whose variant declares every fact stated here.

    The variant publishes no PV device and has no commissioned-system circuits, so the
    template's solar circuits and their locks come off.
    """
    config = default_config()
    config["hardware_version"] = _HARDWARE
    config.pop("pv", None)
    templates = config["circuit_templates"]
    solar = {name for name, t in templates.items() if t.get("device_type") == "pv"}
    config["circuits"] = [c for c in config["circuits"] if c["template"] not in solar]
    for template in templates.values():
        template.pop("commissioned_system", None)
    panel = config["panel_config"]
    panel["site"] = _SITE
    panel["off_grid_import_limit_enablement"] = "ENABLED"
    panel["off_grid_import_limit_a"] = 47.9
    panel["operator_import_limit_enablement"] = "DISABLED"
    config["lugs"] = _LUGS
    config["outside_meters"] = [{"id": "service"}]
    circuit = config["circuits"][0]
    circuit["tags"] = _TAGS
    circuit["locations"] = _LOCATIONS
    circuit["dedicated"] = True
    circuit["nominal_voltage"] = 120.0
    circuit["protection_functions"] = _PROTECTION
    return config


@pytest.mark.asyncio
async def test_a_published_and_cloned_config_keeps_every_fact(tmp_path: Path) -> None:
    config = _commissioned()
    serial = config["panel_config"]["serial_number"]
    retained = await capture_retained(write_config(tmp_path / "panel.yaml", config))

    clone = translate_panel_tree(serial, discovered_devices(retained))

    assert clone["hardware_version"] == _HARDWARE
    panel = clone["panel_config"]
    assert isinstance(panel, dict)
    assert panel["site"] == _SITE
    assert panel["off_grid_import_limit_enablement"] == "ENABLED"
    assert panel["off_grid_import_limit_a"] == 47.9
    assert panel["operator_import_limit_enablement"] == "DISABLED"
    assert clone["lugs"] == _LUGS
    assert clone["outside_meters"] == [{"id": "outside_meter_1"}]
    circuits = clone["circuits"]
    assert isinstance(circuits, list)
    first = config["circuits"][0]
    [cloned] = [c for c in circuits if c["tabs"] == first["tabs"]]
    assert cloned["tags"] == _TAGS
    assert cloned["locations"] == _LOCATIONS
    assert cloned["dedicated"] is True
    assert cloned["nominal_voltage"] == 120.0
    assert cloned["protection_functions"] == _PROTECTION


@pytest.mark.asyncio
async def test_a_meter_outside_the_panel_is_a_circuit_device_with_a_meter_alone(
    tmp_path: Path,
) -> None:
    config = _commissioned()
    serial = config["panel_config"]["serial_number"]
    retained = await capture_retained(write_config(tmp_path / "panel.yaml", config))

    meter = discovered_devices(retained)[stable_circuit_uuid(serial, "service")]

    assert (meter.description or {}).get("type") == "energy.ebus.device.circuit"
    assert set((meter.description or {}).get("nodes") or {}) == {"meter"}
    assert meter.get_property("meter", "active-power") is not None


def test_a_fact_the_config_leaves_out_is_not_published() -> None:
    config = default_config()

    [panel] = [i for i in build_manifest(config).instances if i.entity_class == "panel"]

    assert "site-name" not in panel.metadata
    assert "off-grid-import-limit-enablement" not in panel.metadata


def test_a_rating_or_priority_recorded_as_absent_is_left_unvalued() -> None:
    """A null rating or pcs priority says the panel publishes none, so none is published,
    whether or not the circuit also lists it."""
    config = default_config()
    circuit = config["circuits"][0]
    circuit["pcs_priority"] = None
    config["circuit_templates"][circuit["template"]]["breaker_rating"] = None
    uuid = stable_circuit_uuid(config["panel_config"]["serial_number"], circuit["id"])

    [instance] = [i for i in build_manifest(config).instances if i.instance_id == uuid]

    assert instance.metadata["unvalued"] == "breaker/rating,pcs/priority"


def test_an_outside_meter_sharing_a_circuits_id_is_refused() -> None:
    """Its device id is scoped from its id as a circuit's is, so the two would collide."""
    config = _commissioned()
    config["outside_meters"] = [{"id": config["circuits"][0]["id"]}]

    with pytest.raises(ValueError, match="already taken"):
        validate_yaml_config(config)


def test_an_outside_meter_on_hardware_whose_variant_has_none_is_refused() -> None:
    config = _commissioned()
    config["hardware_version"] = "1.2"

    with pytest.raises(ValueError, match="outside_meters are not published"):
        validate_yaml_config(config)
