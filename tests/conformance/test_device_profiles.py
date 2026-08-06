from __future__ import annotations

import json
from pathlib import Path

from span_panel_simulator.conformance.device_profiles import load_device_profiles
from span_panel_simulator.conformance.model import build_tree
from span_panel_simulator.conformance.rules import Bucket, check_profile_coverage


def _profile_dir(tmp_path: Path) -> Path:
    (tmp_path / "bess.json").write_text(
        json.dumps(
            {
                "device": "energy.ebus.device.bess",
                "device_types": {
                    "energy.ebus.device.bess": {
                        "role": "parent",
                        "capabilities": {
                            "info": {
                                "catalog": "energy.ebus.capability.info",
                                "req": "MUST",
                            },
                            "soc": {
                                "catalog": "energy.ebus.capability.soc",
                                "req": "MUST",
                            },
                        },
                    }
                },
            }
        )
    )
    return tmp_path


def test_loads_profiles_keyed_by_device_type(tmp_path: Path) -> None:
    profiles = load_device_profiles(_profile_dir(tmp_path))
    profile = profiles["energy.ebus.device.bess"]
    assert profile.capabilities == {
        "info": "energy.ebus.capability.info",
        "soc": "energy.ebus.capability.soc",
    }


def test_o8_reports_a_composed_capability_the_device_omits(tmp_path: Path) -> None:
    profiles = load_device_profiles(_profile_dir(tmp_path))
    tree = build_tree(
        {
            "bess-1": {
                "name": "BESS",
                "type": "energy.ebus.device.bess",
                "children": [],
                "nodes": {
                    "soc": {
                        "name": "S",
                        "type": "energy.ebus.capability.soc",
                        "properties": {},
                    }
                },
            }
        }
    )
    findings = check_profile_coverage(tree, profiles)
    assert [(f.rule, f.node, f.bucket) for f in findings] == [("O8", "info", Bucket.OMISSION)]


def test_o8_silent_for_an_unprofiled_device_type(tmp_path: Path) -> None:
    profiles = load_device_profiles(_profile_dir(tmp_path))
    tree = build_tree(
        {"x-1": {"name": "X", "type": "vendor.custom.device", "children": [], "nodes": {}}}
    )
    assert check_profile_coverage(tree, profiles) == []
