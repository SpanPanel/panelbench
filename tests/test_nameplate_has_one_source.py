"""A PV circuit's nameplate has one source of truth: ``energy_profile.nameplate_capacity_w``.

That is the key the dashboard edits and every reader reads. The eBus emitter's
example configs write the rating at the template's top level, so the loader still
reads a top-level ``nameplate_capacity_w``, but only where the profile has none.
It used to copy the top-level value over the profile's, so a template carrying
both, as a copy of the shipped MAIN 40 solar template did, had its dashboard edit
silently replaced the next time the panel started.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ebus_sdk import DiscoveredDevice

from panelbench.clone import TYPE_PV
from panelbench.config_defaults import normalize_circuit_templates
from panelbench.dashboard.config_store import ConfigStore
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from tests._helpers import default_config, write_config


def _solar_with(*, profile: float | None, top_level: float | None) -> dict[str, object]:
    """The default panel, its solar template rated *profile* and *top_level*, either absent."""
    config: dict[str, object] = dict(default_config())
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    solar = templates["solar"]
    assert isinstance(solar, dict)
    energy_profile = solar["energy_profile"]
    assert isinstance(energy_profile, dict)
    energy_profile.pop("nameplate_capacity_w", None)
    solar.pop("nameplate_capacity_w", None)
    if profile is not None:
        energy_profile["nameplate_capacity_w"] = profile
    if top_level is not None:
        solar["nameplate_capacity_w"] = top_level
    return config


async def _nominal_power(path: Path) -> str | None:
    """The one PV device's published ``info/nominal-power``, from the panel *path* starts."""
    devices: dict[str, DiscoveredDevice] = discovered_devices(await capture_retained(path))
    [pv] = [d for d in devices.values() if (d.description or {}).get("type") == TYPE_PV]
    return pv.get_property("info", "nominal-power")


@pytest.mark.asyncio
async def test_a_config_with_both_keys_publishes_the_profile_one(tmp_path: Path) -> None:
    path = write_config(tmp_path / "panel.yaml", _solar_with(profile=3800.0, top_level=10000.0))

    assert await _nominal_power(path) == "3800.0"


@pytest.mark.asyncio
async def test_a_legacy_config_with_only_the_top_level_key_still_works(tmp_path: Path) -> None:
    path = write_config(tmp_path / "panel.yaml", _solar_with(profile=None, top_level=7600.0))

    assert await _nominal_power(path) == "7600.0"


@pytest.mark.asyncio
async def test_a_dashboard_nameplate_edit_survives_a_reload(tmp_path: Path) -> None:
    path = write_config(tmp_path / "panel.yaml", _solar_with(profile=10000.0, top_level=10000.0))
    store = ConfigStore()
    store.load_from_file(path)

    store.update_entity("solar_inverter", {"nameplate_capacity_w": "3800"})
    store.save_to_file(path)

    assert await _nominal_power(path) == "3800.0"


def test_the_top_level_key_still_wins_over_a_default_profile() -> None:
    """A template with no profile gets its device type's default, which is not a value
    the config stated, so a top-level rating the config did state replaces it."""
    config: dict[str, object] = {
        "circuit_templates": {
            "solar": {"device_type": "pv", "nameplate_capacity_w": 7600.0},
        }
    }

    normalize_circuit_templates(config)

    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    solar = templates["solar"]
    assert solar["energy_profile"]["nameplate_capacity_w"] == 7600.0
    assert "nameplate_capacity_w" not in solar, "one source, not two"


def test_a_dashboard_nameplate_edit_clears_a_circuit_override_of_it(tmp_path: Path) -> None:
    """A circuit-level ``overrides.nameplate_capacity_w`` is a second source the engine's
    circuit reads over the template, so the edit removes it, as it does the circuit's
    power overrides."""
    config = _solar_with(profile=10000.0, top_level=None)
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    [solar_circuit] = [c for c in circuits if c["id"] == "solar_inverter"]
    solar_circuit["overrides"] = {"nameplate_capacity_w": 10000.0}
    path = write_config(tmp_path / "panel.yaml", config)
    store = ConfigStore()
    store.load_from_file(path)

    store.update_entity("solar_inverter", {"nameplate_capacity_w": "3800"})

    entity = store.get_entity("solar_inverter")
    assert "nameplate_capacity_w" not in entity.overrides
    assert entity.energy_profile["nameplate_capacity_w"] == 3800.0
