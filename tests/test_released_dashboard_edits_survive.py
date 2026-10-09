"""A nameplate edited in a released dashboard survives the upgrade to one source.

The MAIN 40 and MAIN 32 templates PanelBench 2.5.3 shipped each carried a second
copy of the solar rating: MAIN 40 at the solar template's top level, MAIN 32 in the
solar circuit's ``overrides``. A template clone copied it, and every released
dashboard wrote a nameplate edit to ``energy_profile`` alone, leaving the copy
behind. No released writer ever wrote either copy, so where the two disagree the
profile is the user's edit and the copy is stale: the profile wins, the copy is
dropped, and a WARNING names both values and the file.

The fixtures are those two files exactly as 2.5.3 shipped them, byte for byte:
``git show 4ad9dd4:configs/<file>``, the commit the 2.5.3 version was built from.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard.config_store import ConfigStore
from tests._helpers import default_config, pv_rating, rating_literal, write_config

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig

_RELEASED = Path(__file__).parent / "fixtures" / "released_2_5_3"

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
        published, produced = await pv_rating(path)

    assert published == rating_literal(watts)
    assert produced == (await pv_rating(control))[1]
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        str(path) in message
        and str(stale) in message
        and str(watts) in message
        and "save the config in the dashboard to remove it from the file" in message
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
    published, produced = await pv_rating(path)
    assert published == rating_literal(watts)
    control = write_config(
        tmp_path / "control.yaml",
        _without_the_stale_copy(_edited(name, template, watts), template),
    )
    assert produced == (await pv_rating(control))[1]


@pytest.mark.parametrize(("name", "template", "stale", "watts"), _PANELS)
@pytest.mark.asyncio
async def test_a_template_clone_writes_the_stale_copy_out_once(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    name: str,
    template: str,
    stale: float,
    watts: float,
) -> None:
    """Cloning is a write the user asked for, so the clone holds the rating once and
    the stale copy is warned about once, not by every reader after."""
    write_config(tmp_path / "edited.yaml", _edited(name, template, watts))
    write_config(tmp_path / "active.yaml", default_config())
    app = create_dashboard_app(
        DashboardContext(
            config_dir=tmp_path,
            config_filter="active.yaml",
            get_panel_configs=lambda: {},
            get_panel_ports=lambda: {},
            request_reload=lambda: None,
        )
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        async with TestClient(TestServer(app)) as client:
            cloned = await client.post(
                "/clone", data={"filename": "copy.yaml", "source_file": "edited.yaml"}
            )

    assert cloned.status == 200
    assert _stale_copies(yaml.safe_load((tmp_path / "copy.yaml").read_text()), template) == []
    warned = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "stale" in r.getMessage()
    ]
    assert len(warned) == 1, warned


@pytest.mark.parametrize(("name", "template", "stale", "watts"), _PANELS)
@pytest.mark.asyncio
async def test_a_released_template_clone_still_runs_unedited(
    tmp_path: Path, name: str, template: str, stale: float, watts: float
) -> None:
    """Beside the R1 edit, the files pay for a broader guard: a 2.5.3 template clone,
    with every key and shape of its era, still loads and starts a panel."""
    path = tmp_path / "panel.yaml"
    path.write_text((_RELEASED / name).read_text(encoding="utf-8"), encoding="utf-8")
    ConfigStore().load_from_file(path)

    published, produced = await pv_rating(path)

    assert published == rating_literal(stale), (
        "its stale copy agrees with its profile, so nothing drops"
    )
    assert produced > 0
