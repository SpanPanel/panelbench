"""The hardware version a panel reports over MQTT and, from SPAN release 202639, REST.

One config value, `hardware_version`, is published as `info/hardware-version` (a free
string in SPAN's MQTT topic reference) and, from release 202639, reported as
`GET /api/v2/status`'s required `hardwareVersion`, which SPAN documents as `1.2` or
`2.0`, and `UNKNOWN` when the panel cannot determine it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from panelbench.clone import translate_panel_tree
from panelbench.config_types import SimulationConfig
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from panelbench.hardware import (
    DEFAULT_HARDWARE_VERSION,
    panel_hardware_version,
    status_hardware_version,
)
from tests._helpers import (
    CURRENT_FIRMWARE,
    DEFAULT_CONFIG,
    EARLIER_FIRMWARE,
    default_config,
    write_config,
)

_SERIAL = default_config()["panel_config"]["serial_number"]


def _panel(*, firmware: str, hardware: str | None) -> SimulationConfig:
    """The default panel at *firmware*, with *hardware* as its hardware version or none."""
    config = default_config()
    config["firmware_version"] = firmware
    config.pop("hardware_version", None)
    if hardware is not None:
        config["hardware_version"] = hardware
    return config


def test_an_unset_hardware_version_is_the_default() -> None:
    config = _panel(firmware="sim/v0.1.0", hardware=None)

    assert panel_hardware_version(config) == DEFAULT_HARDWARE_VERSION


def test_an_unquoted_yaml_hardware_version_reads_as_written() -> None:
    """YAML reads `hardware_version: 1.2` as a float; the panel must still report "1.2"."""
    config = _panel(firmware=CURRENT_FIRMWARE, hardware=None)
    config.update(yaml.safe_load("hardware_version: 1.2\n"))

    assert panel_hardware_version(config) == "1.2"
    assert status_hardware_version(config) == "1.2"


@pytest.mark.parametrize("model", ["MAIN_16", "MAIN_32", "MAIN_40"])
def test_every_shipped_template_reports_a_documented_hardware_version(model: str) -> None:
    """Over MQTT and, on release 202639, over REST, and the same value on both."""
    shipped: SimulationConfig = yaml.safe_load(
        DEFAULT_CONFIG.with_name(f"default_{model}.yaml").read_text(encoding="utf-8")
    )
    shipped["firmware_version"] = CURRENT_FIRMWARE

    assert status_hardware_version(shipped) == panel_hardware_version(shipped) == "1.2"


def test_a_panel_that_names_none_reports_one_on_mqtt_and_rest_alike() -> None:
    """The REST status reads the value MQTT publishes: one key, never two answers."""
    config = _panel(firmware=CURRENT_FIRMWARE, hardware=None)

    assert status_hardware_version(config) == panel_hardware_version(config)
    assert status_hardware_version(config) != "UNKNOWN"


def test_before_202639_the_status_still_leaves_the_field_out() -> None:
    assert status_hardware_version(_panel(firmware=EARLIER_FIRMWARE, hardware=None)) is None


@pytest.mark.parametrize(
    ("firmware", "hardware", "reported"),
    [
        (CURRENT_FIRMWARE, "1.2", "1.2"),
        (CURRENT_FIRMWARE, "2.0", "2.0"),
        (CURRENT_FIRMWARE, "rev2", "UNKNOWN"),
        ("sim/v0.1.0", "2.0", "2.0"),
        (EARLIER_FIRMWARE, "1.2", None),
    ],
)
def test_the_status_reports_a_documented_hardware_version_from_202639(
    firmware: str, hardware: str, reported: str | None
) -> None:
    assert status_hardware_version(_panel(firmware=firmware, hardware=hardware)) == reported


@pytest.mark.asyncio
async def test_a_configured_hardware_version_overrides_the_default_on_mqtt_and_rest(
    tmp_path: Path,
) -> None:
    """`2.0`, not the default, so a panel that ignored its config could not pass."""
    config = _panel(firmware=CURRENT_FIRMWARE, hardware="2.0")

    retained = await capture_retained(write_config(tmp_path / "panel.yaml", config))

    assert retained[f"ebus/5/{_SERIAL}/info/hardware-version"].decode() == "2.0"
    assert status_hardware_version(config) == "2.0"


@pytest.mark.asyncio
async def test_a_clone_keeps_the_source_hardware_version(tmp_path: Path) -> None:
    path = write_config(tmp_path / "panel.yaml", _panel(firmware=CURRENT_FIRMWARE, hardware="2.0"))
    devices = discovered_devices(await capture_retained(path))

    cloned = translate_panel_tree(_SERIAL, devices)

    assert cloned["hardware_version"] == "2.0"
