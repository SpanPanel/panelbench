"""Inverters no circuit feeds, each a device of its own, as release 202639 publishes them.

A panel whose inverters sit where no circuit of its own feeds them, ahead of the
panel beside a battery, publishes each as a PV device fed by nothing. The `pv`
section's own keys describe one inverter, so such inverters are listed in its
`inverters`, each with its own identity and rating; the section's own keys then
describe only an inverter a PV circuit feeds. One such inverter on a panel with no
PV circuit may still be the section itself, as a panel before 202639 publishes its
one aggregate inverter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from ebus_panel_sim import DeviceInstance

from panelbench.config_types import PVConfigYAML, PVInverterYAML, SimulationConfig
from panelbench.emitter_adapter.instance_ids import pv_device_id, stable_circuit_uuid
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.validation import validate_yaml_config
from tests._helpers import (
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
    default_config,
    name_firmware,
    source_and_clone,
)

from .test_pv_inverters import _pv_info

_SERIAL = default_config()["panel_config"]["serial_number"]
_FIRMWARE = "sim-pv/v0.1.0"


def _inverter(vendor: str, model: str, rating: float, **more: str) -> PVInverterYAML:
    inverter: PVInverterYAML = {
        "vendor": vendor,
        "product_name": model,
        "nameplate_capacity_w": rating,
    }
    if "serial_number" in more:
        inverter["serial_number"] = more["serial_number"]
    if "relative_position" in more:
        position = more["relative_position"]
        assert position in ("UPSTREAM", "DOWNSTREAM", "IN_PANEL")
        inverter["relative_position"] = position
    return inverter


def _unfed_panel(*inverters: PVInverterYAML, firmware: str = CURRENT_FIRMWARE) -> SimulationConfig:
    """The default panel with no PV circuit, its inverters the section's `inverters`."""
    config = default_config()
    name_firmware(config, firmware)
    solar = [c for c in config["circuits"] if c["template"] == "solar"]
    config["circuits"] = [c for c in config["circuits"] if c["template"] != "solar"]
    config["unmapped_tabs"] = sorted(
        [*config["unmapped_tabs"], *(tab for c in solar for tab in c["tabs"])]
    )
    config["pv"] = {"enabled": True, "firmware_version": _FIRMWARE, "inverters": list(inverters)}
    return config


def _two_unfed(firmware: str = CURRENT_FIRMWARE) -> SimulationConfig:
    return _unfed_panel(
        _inverter("SolarEdge", "SE7600H-US", 11680.0),
        _inverter("Fronius", "Primo 7.6", 7600.0, relative_position="IN_PANEL"),
        firmware=firmware,
    )


def _section(config: SimulationConfig) -> PVConfigYAML:
    section = config.get("pv")
    assert section is not None
    return section


def _pvs(config: SimulationConfig) -> list[DeviceInstance]:
    return [i for i in build_manifest(config).instances if i.entity_class == "pv"]


def test_each_listed_inverter_is_a_device_fed_by_no_circuit() -> None:
    config = _two_unfed()
    validate_yaml_config(config)

    first, second = _pvs(config)

    assert (first.instance_id, second.instance_id) == (f"{_SERIAL}-pv-1", f"{_SERIAL}-pv-2")
    assert "feed" not in first.metadata
    assert "feed" not in second.metadata
    assert (first.metadata["vendor-name"], first.metadata["model"]) == ("SolarEdge", "SE7600H-US")
    assert (second.metadata["vendor-name"], second.metadata["model"]) == ("Fronius", "Primo 7.6")
    assert (first.metadata["nominal-power-w"], second.metadata["nominal-power-w"]) == (
        "11680.0",
        "7600.0",
    )


def test_a_listed_inverter_is_upstream_unless_it_says_otherwise() -> None:
    """As the section's own inverter is placed where no circuit feeds it."""
    first, second = _pvs(_two_unfed())

    assert first.metadata["relative-position"] == "UPSTREAM"
    assert second.metadata["relative-position"] == "IN_PANEL"


def test_a_listed_inverter_takes_the_section_firmware_unless_it_has_its_own() -> None:
    """A firmware version is not identity, so the section's is every inverter's default."""
    config = _two_unfed()
    _section(config)["inverters"][1]["firmware_version"] = "inverter/v9.9.9"

    first, second = _pvs(config)

    assert first.metadata["firmware-version"] == _FIRMWARE
    assert second.metadata["firmware-version"] == "inverter/v9.9.9"


def test_a_listed_inverter_serial_names_its_device() -> None:
    config = _unfed_panel(
        _inverter("SolarEdge", "SE7600H-US", 11680.0, serial_number="7E1A-01"),
        _inverter("SolarEdge", "SE7600H-US", 11680.0, serial_number="7E1A-02"),
    )

    ids = [pv.instance_id for pv in _pvs(config)]

    assert ids == [f"{_SERIAL}-7E1A-01", f"{_SERIAL}-7E1A-02"]


def test_a_listed_inverter_beside_a_pv_circuit_is_numbered_after_it() -> None:
    """The section's own keys describe the inverter its PV circuit feeds, as before."""
    config = default_config()
    _section(config)["inverters"] = [_inverter("Fronius", "Primo 7.6", 7600.0)]
    validate_yaml_config(config)

    fed, unfed = _pvs(config)

    assert fed.instance_id == pv_device_id(_SERIAL, config.get("pv"))
    assert fed.metadata["model"] == "IQ8PLUS-72-2-US"
    assert fed.metadata["feed"] == stable_circuit_uuid(_SERIAL, "solar_inverter")
    assert unfed.instance_id == f"{_SERIAL}-pv-2"
    assert unfed.metadata["model"] == "Primo 7.6"
    assert "feed" not in unfed.metadata


def test_a_disabled_section_publishes_no_listed_inverter() -> None:
    config = _two_unfed()
    _section(config)["enabled"] = False

    assert _pvs(config) == []


@pytest.mark.asyncio
async def test_a_clone_republishes_every_listed_inverter(tmp_path: Path) -> None:
    source, clone = await source_and_clone(tmp_path, _two_unfed())

    assert _pv_info(clone, "model") == _pv_info(source, "model") == ["Primo 7.6", "SE7600H-US"]
    assert (
        _pv_info(clone, "nominal-power") == _pv_info(source, "nominal-power") == ["11680", "7600"]
    )


_SE: Final = {"vendor": "SolarEdge", "product_name": "SE7600H-US", "nameplate_capacity_w": 11680.0}


@pytest.mark.parametrize(
    ("section", "reason"),
    [
        ({"enabled": True, "inverters": []}, "must be a list"),
        ({"enabled": True, "inverters": "SE7600H-US"}, "must be a list"),
        ({"enabled": True, "inverters": [_SE, "SE7600H-US"]}, r"inverters\[1\] must be a mapping"),
        ({"enabled": True, "inverters": [{**_SE, "model": "SE7600H-US"}]}, "has model"),
        (
            {"enabled": True, "inverters": [{**_SE, "relative_position": "ROOF"}]},
            "relative_position is 'ROOF'",
        ),
        ({"enabled": True, "inverters": [{**_SE, "inverter_type": "string"}]}, "inverter_type"),
        ({"enabled": True, "inverters": [{**_SE, "nameplate_capacity_w": 0}]}, "above zero"),
        ({"enabled": True, "inverters": [{**_SE, "nameplate_capacity_w": True}]}, "above zero"),
        (
            {"enabled": True, "vendor": "SolarEdge", "inverters": [_SE]},
            "no PV circuit for them to describe",
        ),
    ],
)
def test_a_listed_inverter_is_refused_unless_it_describes_one(
    section: dict[str, object], reason: str
) -> None:
    config: dict[str, object] = {**_unfed_panel(), "pv": section}

    with pytest.raises(ValueError, match=reason):
        validate_yaml_config(config)


def test_two_listed_inverters_may_not_share_an_identifier() -> None:
    config = _unfed_panel(
        _inverter("SolarEdge", "SE7600H-US", 11680.0, serial_number="7E1A-01"),
        _inverter("SolarEdge", "SE7600H-US", 11680.0, serial_number="7E1A-01"),
    )

    with pytest.raises(ValueError, match=r"inverters\[1\] has the identifier '7E1A-01'"):
        validate_yaml_config(config)


def test_a_listed_inverter_may_not_share_the_fed_inverter_identifier() -> None:
    config = default_config()
    _section(config)["serial_number"] = "7E1A-01"
    _section(config)["inverters"] = [
        _inverter("Fronius", "Primo 7.6", 7600.0, serial_number="7E1A-01")
    ]

    with pytest.raises(ValueError, match="which the inverter a PV circuit feeds already has"):
        validate_yaml_config(config)


def test_several_inverters_are_refused_before_202639() -> None:
    """That release publishes one solar device."""
    with pytest.raises(ValueError, match="names a release before 202639"):
        validate_yaml_config(_two_unfed(EARLIER_FIRMWARE))


def test_one_listed_inverter_is_accepted_before_202639() -> None:
    validate_yaml_config(
        _unfed_panel(_inverter("Enphase", "IQ7PLUS", 4640.0), firmware=EARLIER_FIRMWARE)
    )


def test_listed_inverters_are_refused_by_a_variant_publishing_no_inverter() -> None:
    config = _two_unfed()
    config["hardware_version"] = "3.0"
    for template in config["circuit_templates"].values():
        template.pop("commissioned_system", None)

    with pytest.raises(ValueError, match="publishes no inverter device"):
        validate_yaml_config(config)
