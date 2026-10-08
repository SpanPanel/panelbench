"""A clone keeps the battery's identity and each SPAN Drive's.

A clone reproduces the panel it reads. It already carried the panel's firmware and
each PV inverter's identity; the battery's and the drives' were dropped, so a clone
published no battery model and every drive fell back to PanelBench's own firmware
default and to a serial derived from its position among the clone's circuits.

A drive's identity belongs to the circuit that feeds it, as an inverter's does: its
serial is what a consumer keys the drive on, so it must follow the drive, not the
order the clone happens to walk circuits in. Its firmware version is not identity,
so the `evse` section's is every drive's default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from panelbench.clone import TYPE_BESS, TYPE_EVSE
from panelbench.emitter_adapter.spec_generator import build_manifest
from tests._helpers import default_config, source_and_clone

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ebus_sdk import DiscoveredDevice

    from panelbench.config_types import CircuitDefinitionExtended, SimulationConfig

_GARAGE = "SPAN Drive - Garage"
_DRIVEWAY = "SPAN Drive - Driveway"

_IDENTITY = ("vendor-name", "model", "part-number", "serial-number", "firmware-version")


def _info(
    devices: Mapping[str, DiscoveredDevice], device_type: str, prop: str
) -> dict[str, str | None]:
    """Each published device of *device_type*, by its name, with its `info/<prop>`."""
    out: dict[str, str | None] = {}
    for device in devices.values():
        description: Mapping[str, object] = device.description or {}
        if description.get("type") == device_type:
            out[str(description.get("name"))] = device.get_property("info", prop)
    return out


def _circuit(config: SimulationConfig, name: str) -> CircuitDefinitionExtended:
    """*config*'s circuit called *name*, to modify in place."""
    [circuit] = [c for c in config["circuits"] if c["name"] == name]
    return circuit


def _manifest_firmware(config: SimulationConfig) -> dict[str, str]:
    """Each drive's manifest `firmware-version`, by its name."""
    return {
        evse.display_name: evse.metadata["firmware-version"]
        for evse in build_manifest(config).of_class("evse")
    }


@pytest.mark.asyncio
async def test_a_clone_republishes_the_battery_identity(tmp_path: Path) -> None:
    config = default_config()
    bess = config.get("bess")
    assert bess is not None
    # Not PanelBench's own default, so an agreement cannot be a coincidence.
    bess["vendor"] = "Example Energy"

    source, clone = await source_and_clone(tmp_path, config)

    # One battery each side, named after its own device id, which differs between
    # a panel and its clone; so compared by value, not by name.
    assert list(_info(clone, TYPE_BESS, "model").values()) == ["SPAN Battery"]
    for prop in _IDENTITY:
        [published] = _info(source, TYPE_BESS, prop).values()
        assert published, f"the source publishes {prop}"
        assert list(_info(clone, TYPE_BESS, prop).values()) == [published], prop


@pytest.mark.asyncio
async def test_a_clone_republishes_each_drive_firmware(tmp_path: Path) -> None:
    config = default_config()
    _circuit(config, _GARAGE)["firmware_version"] = "drive/v1.0.0"
    _circuit(config, _DRIVEWAY)["firmware_version"] = "drive/v2.0.0"

    source, clone = await source_and_clone(tmp_path, config)

    expected = {_GARAGE: "drive/v1.0.0", _DRIVEWAY: "drive/v2.0.0"}
    assert _info(source, TYPE_EVSE, "firmware-version") == expected
    assert _info(clone, TYPE_EVSE, "firmware-version") == expected


def test_a_drive_circuit_firmware_wins_over_the_evse_section() -> None:
    config = default_config()
    config["evse"] = {"firmware_version": "evse/v5.0.0"}
    _circuit(config, _GARAGE)["firmware_version"] = "drive/v1.0.0"

    assert _manifest_firmware(config) == {_GARAGE: "drive/v1.0.0", _DRIVEWAY: "evse/v5.0.0"}


def test_a_drive_with_no_firmware_anywhere_publishes_the_default() -> None:
    config = default_config()
    assert not config.get("evse"), "the shipped default names no evse section"

    assert _manifest_firmware(config) == {_GARAGE: "sim/v0.1.0", _DRIVEWAY: "sim/v0.1.0"}


@pytest.mark.parametrize("reverse", [False, True], ids=["in-order", "reversed"])
@pytest.mark.asyncio
async def test_a_clone_keeps_each_drive_serial_on_its_own_drive(
    tmp_path: Path, reverse: bool
) -> None:
    """Each drive's serial, on that drive, whichever order the source lists them in.

    The source derives its drives' serials from their position, so reversing the
    circuits swaps which drive holds the unsuffixed one. The clone must follow the
    drive rather than re-derive from its own circuit order, which sorts by device id.
    """
    config = default_config()
    if reverse:
        config["circuits"].reverse()

    source, clone = await source_and_clone(tmp_path, config)

    serials = _info(source, TYPE_EVSE, "serial-number")
    assert set(serials) == {_GARAGE, _DRIVEWAY}
    assert len(set(serials.values())) == 2, "two drives, two serials"
    assert _info(clone, TYPE_EVSE, "serial-number") == serials
