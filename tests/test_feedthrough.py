"""A panel without a feedthrough module publishes no downstream lugs from release 202645.

From SPAN release 202645 a hardware 3.0 panel without a feedthrough publishes its
upstream lugs alone, as the emitter's r202645 captures show. Earlier releases publish
both whatever the panel has, as every release 202639 capture does, and no capture shows
a MAIN 32 leaving them out. ``panel_config.feedthrough: false`` records the hardware
fact, and the lugs it publishes follow the panel's variant and firmware.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Final

import pytest
import yaml
from ebus_panel_sim import load_reference_capture

from panelbench.clone import translate_panel_tree
from panelbench.emitter_adapter.instance_ids import lugs_device_ids
from panelbench.emitter_adapter.wire_capture import recorded_panel
from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS, discovered_from_tree, panel_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.config_types import SimulationConfig

_R202645: Final = "spanos3/r202645/02"
_R202639: Final = "spanos3/r202639/03"


def _hardware_3(*, firmware: str | None, feedthrough: bool | None = None) -> SimulationConfig:
    """The tracked hardware 3.0 template on *firmware*, without its downstream lugs'
    commissioning, with ``feedthrough`` set unless it is None."""
    config: SimulationConfig = yaml.safe_load(
        (CONFIGS / "ref_MLO_48_TANDEMS.yaml").read_text(encoding="utf-8")
    )
    if firmware is None:
        config.pop("firmware_version", None)
    else:
        config["firmware_version"] = firmware
    lugs = config.get("lugs")
    if lugs is not None:
        lugs.pop("downstream", None)
    if feedthrough is not None:
        config["panel_config"]["feedthrough"] = feedthrough
    return config


async def _published_lugs(tmp_path: Path, config: SimulationConfig) -> set[str]:
    """Which of the panel's two lugs devices it publishes, as its children."""
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", config))
    del runtime
    serial = config["panel_config"]["serial_number"]
    upstream, downstream = lugs_device_ids(config)
    children = set(json.loads(recorder.retained[f"ebus/5/{serial}/$description"])["children"])
    published = {topic.split("/")[2] for topic in recorder.retained}
    return {
        direction
        for direction, device_id in (("upstream", upstream), ("downstream", downstream))
        if device_id in children and device_id in published
    }


@pytest.mark.asyncio
async def test_a_hardware_3_panel_on_202645_without_a_feedthrough_publishes_upstream_lugs_only(
    tmp_path: Path,
) -> None:
    assert await _published_lugs(tmp_path, _hardware_3(firmware=_R202645, feedthrough=False)) == {
        "upstream"
    }


@pytest.mark.asyncio
async def test_a_firmware_naming_no_release_is_taken_to_be_current(tmp_path: Path) -> None:
    """As the emitter takes it: a simulator string gets release 202645's behaviour."""
    assert await _published_lugs(tmp_path, _hardware_3(firmware=None, feedthrough=False)) == {
        "upstream"
    }


@pytest.mark.parametrize(
    ("firmware", "feedthrough"),
    [
        pytest.param(_R202639, False, id="release 202639 publishes both"),
        pytest.param(_R202645, True, id="with a feedthrough"),
        pytest.param(_R202645, None, id="by default"),
    ],
)
@pytest.mark.asyncio
async def test_otherwise_a_hardware_3_panel_publishes_both_lugs(
    tmp_path: Path, firmware: str, feedthrough: bool | None
) -> None:
    published = await _published_lugs(
        tmp_path, _hardware_3(firmware=firmware, feedthrough=feedthrough)
    )
    assert published == {"upstream", "downstream"}


@pytest.mark.asyncio
async def test_a_main_32_publishes_both_lugs_whatever_its_feedthrough(tmp_path: Path) -> None:
    """No capture shows a span-variant panel leaving its downstream lugs out."""
    config = panel_config()
    config["firmware_version"] = _R202645
    config["panel_config"]["feedthrough"] = False
    assert await _published_lugs(tmp_path, config) == {"upstream", "downstream"}


@pytest.mark.parametrize("value", ["false", "no", 0, None, ""])
def test_a_feedthrough_that_is_not_true_or_false_is_refused(value: object) -> None:
    raw = yaml.safe_load(yaml.safe_dump(dict(_hardware_3(firmware=_R202645))))
    raw["panel_config"]["feedthrough"] = value
    with pytest.raises(ValueError, match=r"panel_config\.feedthrough must be true or false"):
        validate_yaml_config(raw)


def test_downstream_lugs_commissioning_on_a_panel_that_publishes_none_is_refused() -> None:
    config = _hardware_3(firmware=_R202645, feedthrough=False)
    config.setdefault("lugs", {})["downstream"] = {"feeds_role": "UNUSED"}
    with pytest.raises(ValueError, match=r"lugs\.downstream"):
        validate_yaml_config(config)


def test_a_battery_downstream_of_lugs_the_panel_does_not_publish_is_refused() -> None:
    """A battery placed downstream hangs from the downstream lugs, which a hardware 3.0
    panel without a feedthrough does not publish from release 202645."""
    config = _hardware_3(firmware=_R202645, feedthrough=False)
    battery = copy.deepcopy(panel_config()["bess"])
    battery["relative_position"] = "DOWNSTREAM"
    battery.pop("feed", None)
    config["bess"] = battery
    with pytest.raises(ValueError, match=r"DOWNSTREAM"):
        validate_yaml_config(config)


def test_on_release_202639_downstream_lugs_commissioning_stands_without_a_feedthrough() -> None:
    """That release publishes the downstream lugs whatever the panel has, so their
    commissioning still describes them."""
    config = _hardware_3(firmware=_R202639, feedthrough=False)
    config.setdefault("lugs", {})["downstream"] = {"feeds_role": "UNUSED"}
    validate_yaml_config(copy.deepcopy(config))


@pytest.mark.parametrize(
    ("capture", "feedthrough"),
    [
        pytest.param("r202645-c", False, id="a 202645 tree without downstream lugs"),
        pytest.param("r202639-c", None, id="a 202639 tree with both"),
        pytest.param("main32_r202645", None, id="a MAIN 32 with both"),
    ],
)
def test_a_clone_records_a_panel_without_downstream_lugs(
    capture: str, feedthrough: bool | None
) -> None:
    tree = load_reference_capture(capture).tree
    (panel,) = [i for i, d in tree.items() if d.type == "distribution-enclosure"]
    config = translate_panel_tree(panel, discovered_from_tree(tree))
    panel_section = config["panel_config"]
    assert isinstance(panel_section, dict)
    assert panel_section.get("feedthrough") == feedthrough
