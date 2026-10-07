"""A nameplate edited in a released dashboard survives the upgrade to one source.

The MAIN 40 and MAIN 32 templates PanelBench 2.5.3 shipped each carried a second
copy of the solar rating: MAIN 40 at the solar template's top level, MAIN 32 in the
solar circuit's ``overrides``. A template clone copied it, and every released
dashboard wrote a nameplate edit to ``energy_profile`` alone, leaving the copy
behind. No released writer ever wrote either copy, so where the two disagree the
profile is the user's edit and the copy is stale: the profile wins, the copy is
dropped, and a WARNING names both values and the file.

The fixtures are those two files exactly as 2.5.3 shipped them.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
import yaml

from panelbench.clone import TYPE_PV
from panelbench.dashboard.config_store import ConfigStore
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.wire_capture import discovered_devices, recorded_panel
from tests._helpers import write_config

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig

_RELEASED = Path(__file__).parent / "fixtures" / "released_2_5_3"
_NOON = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()

_PANELS = [
    # file, solar template, its stale copy's value, the dashboard edit
    ("default_MAIN_40.yaml", "solar", 10000.0, 7600.0),
    ("default_MAIN_32.yaml", "solar_production", 8000.0, 6000.0),
]


def _edited(name: str, template: str, watts: float) -> SimulationConfig:
    """The released file *name*, with a released dashboard's nameplate edit to *watts*.

    What 2.5.3's ``ConfigStore.update_entity`` wrote: the profile's rating, a power
    range and typical power to match, and the circuit's power overrides cleared.
    Nothing else, so the file's other copy of the rating stays as shipped.
    """
    config: SimulationConfig = yaml.safe_load((_RELEASED / name).read_text(encoding="utf-8"))
    energy_profile = config["circuit_templates"][template]["energy_profile"]
    energy_profile["nameplate_capacity_w"] = watts
    energy_profile["power_range"] = [-watts, 0.0]
    energy_profile["typical_power"] = -watts * 0.6
    for circuit in config["circuits"]:
        if circuit["template"] == template:
            overrides = circuit.get("overrides") or {}
            overrides.pop("typical_power", None)
            overrides.pop("power_range", None)
    return config


def _stale_copies(config: object, template: str) -> list[object]:
    """Every copy of *template*'s rating outside its profile, in *config* as YAML holds it."""
    assert isinstance(config, dict)
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    copies: list[object] = []
    if "nameplate_capacity_w" in templates[template]:
        copies.append(templates[template]["nameplate_capacity_w"])
    for circuit in config["circuits"]:
        overrides = circuit.get("overrides") or {}
        if circuit["template"] == template and "nameplate_capacity_w" in overrides:
            copies.append(overrides["nameplate_capacity_w"])
    return copies


def _without_the_stale_copy(config: SimulationConfig, template: str) -> dict[str, object]:
    """*config* as if its rating had only ever been in the profile: the control."""
    control = yaml.safe_load(yaml.safe_dump(dict(config)))
    assert isinstance(control, dict)
    control["circuit_templates"][template].pop("nameplate_capacity_w", None)
    for circuit in control["circuits"]:
        if circuit["template"] == template:
            (circuit.get("overrides") or {}).pop("nameplate_capacity_w", None)
    return control


async def _rating(path: Path) -> tuple[str | None, float]:
    """The panel at *path*'s published ``info/nominal-power``, and what its solar
    circuit produces at noon."""
    runtime, recorder = await recorded_panel(path)
    await emitter_runtime.publish_tick(runtime)
    devices = discovered_devices(recorder.retained)
    [pv] = [d for d in devices.values() if (d.description or {}).get("type") == TYPE_PV]
    produced = runtime.engine.modelled_circuit_power("solar_inverter", _NOON)
    assert produced > 0, "noon in June, so the inverter is producing"
    return pv.get_property("info", "nominal-power"), produced


@pytest.mark.parametrize(("name", "template", "stale", "watts"), _PANELS)
@pytest.mark.asyncio
async def test_a_released_edit_loads_and_is_the_rating(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    name: str,
    template: str,
    stale: float,
    watts: float,
) -> None:
    config = _edited(name, template, watts)
    path = write_config(tmp_path / "panel.yaml", config)
    control = write_config(tmp_path / "control.yaml", _without_the_stale_copy(config, template))

    with caplog.at_level(logging.WARNING):
        published, produced = await _rating(path)

    assert published == str(watts)
    assert produced == (await _rating(control))[1]
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        str(path) in message and str(stale) in message and str(watts) in message
        for message in warned
    ), warned


@pytest.mark.parametrize(("name", "template", "stale", "watts"), _PANELS)
@pytest.mark.asyncio
async def test_a_released_edit_survives_a_save_and_reload(
    tmp_path: Path, name: str, template: str, stale: float, watts: float
) -> None:
    path = write_config(tmp_path / "panel.yaml", _edited(name, template, watts))
    store = ConfigStore()
    store.load_from_file(path)

    store.save_to_file(path)

    assert _stale_copies(_edited(name, template, watts), template) == [stale]
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert _stale_copies(saved, template) == [], "the stale copy is gone from the file"
    reloaded = ConfigStore()
    reloaded.load_from_file(path)
    assert reloaded.get_entity("solar_inverter").energy_profile["nameplate_capacity_w"] == watts
    published, produced = await _rating(path)
    assert published == str(watts)
    control = write_config(
        tmp_path / "control.yaml",
        _without_the_stale_copy(_edited(name, template, watts), template),
    )
    assert produced == (await _rating(control))[1]
