"""A circuit SPAN adds for a commissioned PV or battery system.

A SPAN panel adds a circuit named "Commissioned PV System" or
"Commissioned Backup System" whose relay is locked and whose load-shed priority
is permanently NEVER, with neither settable. The emitter publishes that from the
circuit metadata `commissioned-system`; PanelBench states it in the config and
reads it back when cloning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
import yaml

from panelbench.clone import translate_panel_tree
from panelbench.config_types import SimulationConfig
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from panelbench.validation import validate_yaml_config
from tests._helpers import EARLIER_FIRMWARE, default_config, write_config

_SERIAL = default_config()["panel_config"]["serial_number"]


def _commissioned_config(
    *,
    system: Literal["pv", "backup"] | None = "pv",
    relay_behavior: str = "non_controllable",
) -> SimulationConfig:
    """The default panel with its solar circuit named and locked as the commissioned PV system.

    *system* is written as the template's `commissioned_system` unless it is None, so
    a test can take the key away and see what the rest of the path does without it.
    """
    config = default_config()
    solar = config["circuit_templates"]["solar"]
    solar["priority"] = "NEVER"
    solar["relay_behavior"] = relay_behavior
    if system is not None:
        solar["commissioned_system"] = system
    for circuit in config["circuits"]:
        if circuit["id"] == "solar_inverter":
            circuit["name"] = "Commissioned PV System"
    return config


def test_the_manifest_carries_the_commissioned_system() -> None:
    config = _commissioned_config()
    circuit_id = stable_circuit_uuid(_SERIAL, "solar_inverter")

    circuit = next(i for i in build_manifest(config).instances if i.instance_id == circuit_id)

    assert circuit.metadata["commissioned-system"] == "pv"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("priority", "OFF_GRID", "priority: NEVER"),
        ("relay_behavior", "controllable", "locked relay"),
        ("commissioned_system", "solar", "'pv' or 'backup'"),
        ("never_backup", True, "never_backup"),
    ],
)
def test_a_contradictory_commissioned_template_is_refused(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    # Read back as plain YAML, so a test can write values the typed config forbids.
    path = write_config(tmp_path / "panel.yaml", _commissioned_config())
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["circuit_templates"]["solar"][field] = value

    with pytest.raises(ValueError, match=message):
        validate_yaml_config(raw)


def test_a_commissioned_circuit_on_a_release_before_202639_is_refused() -> None:
    """SPAN locks these circuits only from release 202639; before it they were
    switchable and re-prioritisable, so a config naming an earlier release cannot
    lock one. A clone of such a panel never sets the key: nothing on its wire is locked."""
    config = _commissioned_config()
    config["firmware_version"] = EARLIER_FIRMWARE

    with pytest.raises(ValueError, match=r"'solar'.*locked only from SPAN release 202639"):
        validate_yaml_config(config)


@pytest.mark.asyncio
async def test_a_clone_keeps_the_commissioned_system(tmp_path: Path) -> None:
    path = write_config(tmp_path / "panel.yaml", _commissioned_config())
    devices = discovered_devices(await capture_retained(path))

    config = translate_panel_tree(_SERIAL, devices)

    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    commissioned = [t for t in templates.values() if t.get("commissioned_system") == "pv"]
    assert len(commissioned) == 1
    assert commissioned[0]["priority"] == "NEVER"
    assert commissioned[0]["relay_behavior"] == "non_controllable"
    assert "never_backup" not in commissioned[0]
    validate_yaml_config(config)


@pytest.mark.asyncio
@pytest.mark.parametrize("relay_behavior", ["controllable", "non_controllable"])
async def test_the_name_without_both_locks_is_not_commissioned(
    tmp_path: Path, relay_behavior: str
) -> None:
    """The name alone is not the lock: capture's rule needs both locks too.

    Without `commissioned_system` the source publishes `load-shed/priority` with
    `$settable`, so the priority lock is missing in both cases; the controllable
    case also lacks the relay lock.
    """
    source = _commissioned_config(system=None, relay_behavior=relay_behavior)
    path = write_config(tmp_path / "panel.yaml", source)

    cloned = translate_panel_tree(_SERIAL, discovered_devices(await capture_retained(path)))

    templates = cloned["circuit_templates"]
    assert isinstance(templates, dict)
    assert not any("commissioned_system" in t for t in templates.values())
    validate_yaml_config(cloned)
