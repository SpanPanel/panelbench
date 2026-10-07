"""A PV circuit's rating has one source of truth: ``energy_profile.nameplate_capacity_w``.

That is the key the dashboard edits and every reader reads. Three older places can
also hold a rating: the template's top level, a circuit's ``overrides``, and the
``pv`` section, which rates the inverter of the circuit it binds. The one
normaliser the engine, the dashboard and the history generator all run folds each
of them into the profile. A legacy value equal to the profile's, or one where the
profile states none, folds silently. Against a stated profile, a stale top-level or
override copy gives way with a WARNING, because only a released template put it
there; a disagreeing ``pv`` section rating is refused, naming both keys and both
values, because only a hand edit puts it there and picking either would be a guess.

Each published rating here is checked against what the engine produces, because
the bug these tests pin was a panel that published one rating and produced at
another.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
import yaml

from panelbench.clone import TYPE_PV
from panelbench.config_defaults import normalize_config
from panelbench.dashboard.config_store import ConfigStore
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.wire_capture import discovered_devices, recorded_panel
from panelbench.validation import validate_yaml_config
from tests._helpers import default_config, write_config

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.config_types import SimulationConfig

_SOLAR = "solar_inverter"
# Midday in June where the default panel sits, so the inverter is producing.
_NOON = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


def _panel(
    *,
    profile: float | None,
    top_level: float | None = None,
    override: float | None = None,
    section: float | None = None,
) -> dict[str, object]:
    """The default panel, its solar circuit rated in each place a value is given for."""
    config: dict[str, object] = copy.deepcopy(dict(default_config()))
    solar = _template(config, "solar")
    energy_profile = solar["energy_profile"]
    assert isinstance(energy_profile, dict)
    energy_profile.pop("nameplate_capacity_w", None)
    if profile is not None:
        energy_profile["nameplate_capacity_w"] = profile
    if top_level is not None:
        solar["nameplate_capacity_w"] = top_level
    if override is not None:
        _circuit(config, _SOLAR)["overrides"] = {"nameplate_capacity_w": override}
    if section is not None:
        pv = config["pv"]
        assert isinstance(pv, dict)
        pv["nameplate_capacity_w"] = section
    return config


def _template(config: dict[str, object], name: str) -> dict[str, object]:
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    template = templates[name]
    assert isinstance(template, dict)
    return template


def _circuit(config: dict[str, object], circuit_id: str) -> dict[str, object]:
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    [circuit] = [c for c in circuits if isinstance(c, dict) and c["id"] == circuit_id]
    return circuit


async def _rating(path: Path) -> tuple[str | None, float]:
    """The panel at *path*'s published ``info/nominal-power``, and what its solar
    circuit produces at noon."""
    runtime, recorder = await recorded_panel(path)
    await emitter_runtime.publish_tick(runtime)
    devices = discovered_devices(recorder.retained)
    [pv] = [d for d in devices.values() if (d.description or {}).get("type") == TYPE_PV]
    produced = runtime.engine.modelled_circuit_power(_SOLAR, _NOON)
    assert produced > 0, "noon in June, so the inverter is producing"
    return pv.get_property("info", "nominal-power"), produced


async def _assert_rated(tmp_path: Path, config: dict[str, object], watts: float) -> None:
    """*config* publishes *watts* and produces exactly what a panel stating only
    ``energy_profile.nameplate_capacity_w: watts`` produces."""
    published, produced = await _rating(write_config(tmp_path / "panel.yaml", config))
    _, expected = await _rating(write_config(tmp_path / "control.yaml", _panel(profile=watts)))
    assert published == str(watts)
    assert produced == expected


@pytest.mark.parametrize(
    "legacy",
    [{"top_level": 7600.0}, {"override": 7600.0}, {"section": 7600.0}],
    ids=["top level", "circuit override", "pv section"],
)
@pytest.mark.asyncio
async def test_a_legacy_rating_with_no_profile_rating_is_used(
    tmp_path: Path, legacy: dict[str, float]
) -> None:
    await _assert_rated(tmp_path, _panel(profile=None, **legacy), 7600.0)


@pytest.mark.parametrize(
    "legacy",
    [{"top_level": 3800.0}, {"override": 3800.0}, {"section": 3800.0}],
    ids=["top level", "circuit override", "pv section"],
)
@pytest.mark.asyncio
async def test_a_legacy_rating_equal_to_the_profile_folds_silently(
    tmp_path: Path, legacy: dict[str, float]
) -> None:
    await _assert_rated(tmp_path, _panel(profile=3800.0, **legacy), 3800.0)


@pytest.mark.parametrize(
    "legacy",
    [{"top_level": 9000.0}, {"override": 9000.0}],
    ids=["top level", "circuit override"],
)
@pytest.mark.asyncio
async def test_a_stale_copy_beside_a_profile_rating_gives_way_to_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, legacy: dict[str, float]
) -> None:
    """No released writer wrote these two, and every released dashboard edit wrote the
    profile, so the profile is the user's and the copy is dropped, with a WARNING."""
    with caplog.at_level(logging.WARNING):
        await _assert_rated(tmp_path, _panel(profile=3800.0, **legacy), 3800.0)

    assert any(
        "9000.0" in record.getMessage() and "3800.0" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


def test_a_pv_section_rating_that_disagrees_with_the_profile_is_refused() -> None:
    """Only a hand edit fills the ``pv`` section's rating, so which value is meant is a
    guess."""
    config = _panel(profile=3800.0, section=9000.0)
    normalize_config(config, source="test")

    with pytest.raises(ValueError, match="nameplate") as refused:
        validate_yaml_config(config)

    message = str(refused.value)
    assert "pv.nameplate_capacity_w is 9000.0" in message
    assert "circuit_templates.solar.energy_profile.nameplate_capacity_w is 3800.0" in message


def test_a_pv_section_rating_that_rates_no_inverter_is_refused() -> None:
    """Several PV circuits and no ``pv.feed``: the section binds none, so its rating
    would rate nothing, and silently dropping it is a guess too."""
    config = _panel(profile=None, section=7600.0)
    _template(config, "solar")["energy_profile"] = {
        "mode": "producer",
        "power_range": [-10000.0, 0.0],
        "typical_power": -6000.0,
        "nameplate_capacity_w": 10000.0,
    }
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    templates["solar_garage"] = copy.deepcopy(templates["solar"])
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    circuits.append(
        {"id": "solar_garage", "name": "Garage", "template": "solar_garage", "tabs": [24]}
    )
    config["firmware_version"] = "spanos3/r202639/03"
    normalize_config(config, source="test")

    with pytest.raises(ValueError, match=r"pv\.nameplate_capacity_w is 7600\.0"):
        validate_yaml_config(config)


def test_a_circuit_override_of_a_shared_template_is_refused() -> None:
    """Folding it into the template would re-rate the other circuit too."""
    config = _panel(profile=None, override=7600.0)
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    circuits.append({"id": "solar_twin", "name": "Twin", "template": "solar", "tabs": [24]})
    pv = config["pv"]
    assert isinstance(pv, dict)
    pv["feed"] = _SOLAR
    normalize_config(config, source="test")

    with pytest.raises(ValueError, match=r"shared with 'solar_twin'"):
        validate_yaml_config(config)


def test_the_top_level_key_still_wins_over_a_default_profile() -> None:
    """A template with no profile gets its device type's default, which is not a value
    the config stated, so a top-level rating the config did state replaces it."""
    config: dict[str, object] = {
        "circuit_templates": {"solar": {"device_type": "pv", "nameplate_capacity_w": 7600.0}},
    }

    normalize_config(config, source="test")

    solar = _template(config, "solar")
    energy_profile = solar["energy_profile"]
    assert isinstance(energy_profile, dict)
    assert energy_profile["nameplate_capacity_w"] == 7600.0
    assert "nameplate_capacity_w" not in solar, "one source, not two"


@pytest.mark.parametrize(
    "legacy", [{"top_level": 7600.0}, {"override": 7600.0}, {"section": 7600.0}]
)
def test_the_dashboard_saves_a_legacy_rating_in_its_one_place(
    tmp_path: Path, legacy: dict[str, float]
) -> None:
    """Loaded and saved, the config holds the rating once, where the form reads it, so
    saving the form cannot re-rate the inverter from the power-range fallback."""
    path = write_config(tmp_path / "panel.yaml", _panel(profile=None, **legacy))
    store = ConfigStore()
    store.load_from_file(path)

    assert store.get_entity(_SOLAR).energy_profile["nameplate_capacity_w"] == 7600.0
    store.update_entity(_SOLAR, {"name": "Solar Inverter"})
    store.save_to_file(path)

    saved: SimulationConfig = yaml.safe_load(path.read_text(encoding="utf-8"))
    solar_template = saved["circuit_templates"]["solar"]
    assert solar_template["energy_profile"]["nameplate_capacity_w"] == 7600.0
    assert "nameplate_capacity_w" not in solar_template
    assert "nameplate_capacity_w" not in (saved.get("pv") or {})
    assert all(
        "nameplate_capacity_w" not in (circuit.get("overrides") or {})
        for circuit in saved["circuits"]
    )


@pytest.mark.asyncio
async def test_a_dashboard_nameplate_edit_survives_a_reload(tmp_path: Path) -> None:
    path = write_config(tmp_path / "panel.yaml", _panel(profile=10000.0, top_level=10000.0))
    store = ConfigStore()
    store.load_from_file(path)

    store.update_entity(_SOLAR, {"nameplate_capacity_w": "3800"})
    store.save_to_file(path)

    published, produced = await _rating(path)
    _, expected = await _rating(write_config(tmp_path / "control.yaml", _panel(profile=3800.0)))
    assert published == "3800.0"
    assert produced == expected


def test_a_pv_section_rating_on_a_shared_template_is_told_to_split_it() -> None:
    """Making the two agree would re-rate the other circuit on the template too, so the
    advice is a template of the rated circuit's own."""
    config = _panel(profile=3800.0, section=9000.0)
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    circuits.append({"id": "solar_twin", "name": "Twin", "template": "solar", "tabs": [24]})
    pv = config["pv"]
    assert isinstance(pv, dict)
    pv["feed"] = _SOLAR
    normalize_config(config, source="test")

    with pytest.raises(ValueError, match="shared with 'solar_twin'") as refused:
        validate_yaml_config(config)

    assert "give the rated circuit its own template" in str(refused.value)
    assert "make the two agree" not in str(refused.value)
