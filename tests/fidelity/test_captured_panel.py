"""A real SPAN panel on release 202639 or later, read by PanelBench's import.

The pinned release ships a masked capture of a panel that had taken the 202639
upgrade. Parity measures the import against upstream's emitter publishing the
same panel; these assert what parity cannot: that the import kept the panel on
the current conventions, kept every commissioned-system circuit, and kept every
inverter.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml
from ebus_panel_sim import ManifestPhysicsView

from panelbench.config_types import SimulationConfig
from panelbench.definition_import import config_from_definition
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import as_capture, capture_retained
from panelbench.firmware import SPAN_RELEASE_202639
from panelbench.hardware import status_hardware_version
from tests._helpers import (
    CAPTURED_MAIN_32_SERIAL,
    CURRENT_FIRMWARE,
    captured_main_32,
    default_config,
    write_config,
)

from .comparator import captured_definition, class_of


def _imported(tmp_path: Path) -> tuple[SimulationConfig, Path]:
    """PanelBench's import of the capture, as loaded from the file it writes."""
    path = write_config(tmp_path / "captured.yaml", config_from_definition(captured_definition()))
    config: SimulationConfig = yaml.safe_load(path.read_text(encoding="utf-8"))
    return config, path


def test_the_capture_is_a_panel_on_202639_or_later() -> None:
    """Guards the premise every other test here rests on."""
    release = ManifestPhysicsView(captured_definition().manifest).panel.release_build

    assert release is not None and release >= SPAN_RELEASE_202639


def test_the_import_keeps_the_panels_release(tmp_path: Path) -> None:
    """The import names the captured release, so the emitter applies its conventions."""
    captured = ManifestPhysicsView(captured_definition().manifest).panel.release_build
    config, _path = _imported(tmp_path)

    imported = ManifestPhysicsView(build_manifest(config)).panel.release_build

    assert imported == captured
    assert imported is not None and imported >= SPAN_RELEASE_202639


def test_the_import_keeps_every_commissioned_system(tmp_path: Path) -> None:
    captured = Counter(
        circuit.metadata["commissioned-system"]
        for circuit in captured_definition().manifest.of_class("circuit")
        if "commissioned-system" in circuit.metadata
    )
    config, _path = _imported(tmp_path)

    imported = Counter(
        template["commissioned_system"]
        for template in config["circuit_templates"].values()
        if "commissioned_system" in template
    )

    assert imported == captured


@pytest.mark.asyncio
async def test_the_import_keeps_every_inverter(tmp_path: Path) -> None:
    captured = len(captured_definition().manifest.of_class("pv"))
    _config, path = _imported(tmp_path)

    published = as_capture(await capture_retained(path))

    assert sum(class_of(body) == "pv" for body in published.values()) == captured


@pytest.mark.asyncio
async def test_a_panel_naming_no_hardware_version_reports_the_captured_panels(
    tmp_path: Path,
) -> None:
    """The default is the form a real panel publishes, not one PanelBench made up: the
    captured MAIN 32's `info/hardware-version`, on MQTT and, from release 202639, as
    the REST status's `hardwareVersion`."""
    captured = captured_main_32()[CAPTURED_MAIN_32_SERIAL].value("info/hardware-version")
    config = default_config()
    config.pop("hardware_version", None)
    config["firmware_version"] = CURRENT_FIRMWARE
    serial = config["panel_config"]["serial_number"]

    retained = await capture_retained(write_config(tmp_path / "panel.yaml", config))

    assert retained[f"ebus/5/{serial}/info/hardware-version"].decode() == captured
    assert status_hardware_version(config) == captured
