from pathlib import Path

import pytest
import yaml

from span_panel_simulator.emitter_adapter.spec_generator import (
    GeneratedArtifacts,
    build_manifest,
    generate,
)


def _profile() -> dict:
    """Load the default_MAIN_40 clone profile fixture."""
    return yaml.safe_load(Path("configs/default_MAIN_40.yaml").read_text())


def test_build_manifest_includes_panel_lugs_and_circuits() -> None:
    manifest = build_manifest(_profile())
    assert len(manifest.of_class("panel")) == 1
    assert len(manifest.of_class("lugs")) == 2
    assert len(manifest.of_class("circuit")) > 0


def test_build_manifest_includes_bess_when_enabled() -> None:
    profile = _profile()
    if profile.get("bess", {}).get("enabled"):
        manifest = build_manifest(profile)
        assert len(manifest.of_class("bess")) == 1
    else:
        pytest.skip("default_MAIN_40 has no enabled BESS")


def test_runtime_spec_panel_id_matches_manifest() -> None:
    artifacts = generate(_profile())
    panel_id = artifacts.manifest.of_class("panel")[0].instance_id
    assert artifacts.runtime_spec.panel.instance_id == panel_id


def test_runtime_spec_normalises_priorities() -> None:
    artifacts = generate(_profile())
    valid = {"NEVER", "SOC_THRESHOLD", "OFF_GRID", "UNKNOWN"}
    for c in artifacts.runtime_spec.circuits:
        assert c.priority in valid


def test_runtime_spec_charge_mode_in_supported_set() -> None:
    artifacts = generate(_profile())
    if artifacts.runtime_spec.bess is None:
        pytest.skip("no BESS in fixture")
    assert artifacts.runtime_spec.bess.charge_mode in ("self-consumption", "backup-only")


def test_runtime_spec_default_setter_debounce_is_15() -> None:
    artifacts = generate(_profile())
    assert artifacts.runtime_spec.panel.setter_debounce_minutes == 15


def test_generate_returns_paired_artifacts() -> None:
    artifacts = generate(_profile())
    assert isinstance(artifacts, GeneratedArtifacts)
    assert isinstance(artifacts.runtime_spec.circuits, tuple)
    assert len(artifacts.runtime_spec.circuits) == len(artifacts.manifest.of_class("circuit"))
