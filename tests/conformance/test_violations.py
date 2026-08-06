from __future__ import annotations

from pathlib import Path

from span_panel_simulator.conformance.catalogs import load_catalogs
from span_panel_simulator.conformance.model import HomieTree, build_tree
from span_panel_simulator.conformance.rules import check_violations

CATALOGS = load_catalogs(Path("src/span_panel_simulator/ebus_emitter/wire/catalogs"))


def _tree(prop: dict[str, object], prop_id: str = "soe") -> HomieTree:
    return build_tree(
        {
            "bess-1": {
                "name": "BESS",
                "type": "energy.ebus.device.bess",
                "children": [],
                "nodes": {
                    "soc": {
                        "name": "State",
                        "type": "energy.ebus.capability.soc",
                        "properties": {prop_id: prop},
                    }
                },
            }
        }
    )


def test_v1_enum_without_format_is_a_violation() -> None:
    findings = check_violations(_tree({"name": "S", "datatype": "enum"}, "status"), CATALOGS)
    assert [f.rule for f in findings] == ["V1"]


def test_v2_abstract_token_on_the_wire_is_a_violation() -> None:
    findings = check_violations(
        _tree({"name": "SoE", "datatype": "float", "unit": "energy"}), CATALOGS
    )
    assert "V2" in [f.rule for f in findings]


def test_v3_missing_unit_where_catalog_is_abstract_is_a_violation() -> None:
    """The defect this whole tool exists for: not a wrong unit, an absent one."""
    findings = check_violations(_tree({"name": "SoE", "datatype": "float"}), CATALOGS)
    assert [f.rule for f in findings] == ["V3"]


def test_v3_passes_when_a_concrete_unit_is_substituted() -> None:
    findings = check_violations(
        _tree({"name": "SoE", "datatype": "float", "unit": "kWh"}), CATALOGS
    )
    assert findings == []


def test_v4_dangling_child_is_a_violation() -> None:
    tree = build_tree(
        {"root-1": {"name": "R", "type": "t", "nodes": {}, "children": ["missing-child"]}}
    )
    findings = check_violations(tree, CATALOGS)
    assert [f.rule for f in findings] == ["V4"]
