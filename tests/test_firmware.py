"""A panel reports one firmware string, and a clone keeps its source's.

The emitter decides from that string which side of SPAN release 202639 the panel
publishes (the BESS meter's sign, the EVSE user limit). PanelBench reads the same
release from it for what the emitter does not derive, by its own copy of the rule,
which must agree with the emitter's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ebus_panel_sim import ManifestPhysicsView

from panelbench.clone import translate_panel_tree
from panelbench.config_types import SimulationConfig
from panelbench.const import DEFAULT_FIRMWARE_VERSION
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from panelbench.firmware import (
    SPAN_RELEASE_202639,
    panel_firmware_version,
    predates,
    release_build,
)
from tests._helpers import CURRENT_FIRMWARE, EARLIER_FIRMWARE, default_config, write_config

_SERIAL = default_config()["panel_config"]["serial_number"]
_EVSE_LIMIT = "/config/user-max-charge-current"


def _naming(firmware: str | None) -> SimulationConfig:
    """The default panel naming *firmware*, or naming none."""
    config = default_config()
    config.pop("firmware_version", None)
    if firmware is not None:
        config["firmware_version"] = firmware
    return config


def test_an_unset_firmware_is_the_package_version() -> None:
    assert panel_firmware_version(_naming(None)) == DEFAULT_FIRMWARE_VERSION


def test_an_unquoted_yaml_firmware_reads_as_a_string() -> None:
    """YAML reads `firmware_version: 202639` as an int; every surface needs the string."""
    config = default_config()
    config.update(yaml.safe_load("firmware_version: 202639\n"))

    assert panel_firmware_version(config) == "202639"


@pytest.mark.parametrize(
    ("firmware", "release"),
    [
        (EARLIER_FIRMWARE, 202633),
        (CURRENT_FIRMWARE, 202639),
        ("spanos2/r999999/01", 999999),  # a synthetic release, past any real one
        ("sim/v0.1.0", None),
        ("spanos2/r2026330/02", None),
    ],
)
def test_panelbench_parses_the_release_as_the_emitter_does(
    firmware: str, release: int | None
) -> None:
    """PanelBench's parse, and the emitter's documented one through the manifest.

    The second also proves the string reaches the manifest unchanged.
    """
    manifest = build_manifest(_naming(firmware))

    assert release_build(firmware) == release
    assert ManifestPhysicsView(manifest).panel.release_build == release


@pytest.mark.parametrize(
    ("firmware", "earlier"),
    [
        ("spanos2/r202638/01", True),
        (CURRENT_FIRMWARE, False),
        ("sim/v0.1.0", False),
    ],
)
def test_a_string_naming_no_release_predates_nothing(firmware: str, earlier: bool) -> None:
    assert predates(firmware, SPAN_RELEASE_202639) is earlier


@pytest.mark.asyncio
async def test_a_config_naming_no_firmware_publishes_the_default_and_current_conventions(
    tmp_path: Path,
) -> None:
    """An existing clone written before clones kept their firmware names none.

    It is not backfilled: it publishes the package's own string, which names no
    SPAN release, and so the current conventions. The CHANGELOG tells its owner to
    set `firmware_version` or clone again.
    """
    retained = await capture_retained(write_config(tmp_path / "panel.yaml", _naming(None)))

    assert retained[f"ebus/5/{_SERIAL}/info/firmware-version"].decode() == DEFAULT_FIRMWARE_VERSION
    assert not [t for t in retained if t.endswith(_EVSE_LIMIT)], "no preset EVSE limit"


@pytest.mark.asyncio
async def test_a_clone_keeps_the_source_firmware(tmp_path: Path) -> None:
    source = write_config(tmp_path / "panel.yaml", _naming(EARLIER_FIRMWARE))
    devices = discovered_devices(await capture_retained(source))

    config = translate_panel_tree(_SERIAL, devices)

    assert config["firmware_version"] == EARLIER_FIRMWARE


@pytest.mark.asyncio
async def test_a_clone_of_an_earlier_panel_keeps_its_conventions(tmp_path: Path) -> None:
    """The clone publishes as its source's release did, not as the simulator's own.

    Asserted on the EVSE limit rather than the BESS meter's sign, because the limit
    is published at the first tick whatever the battery is doing.
    """
    source = write_config(tmp_path / "panel.yaml", _naming(EARLIER_FIRMWARE))
    cloned = translate_panel_tree(_SERIAL, discovered_devices(await capture_retained(source)))

    republished = await capture_retained(write_config(tmp_path / "clone.yaml", cloned))

    limits = [t for t in republished if t.endswith(_EVSE_LIMIT)]
    assert len(limits) == 2, "both cloned SPAN Drives keep an r202633 panel's preset limit"
