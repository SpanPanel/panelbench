from __future__ import annotations

from pathlib import Path

from span_panel_simulator.conformance.catalogs import load_catalogs
from span_panel_simulator.conformance.model import HomieTree, build_tree
from span_panel_simulator.conformance.rules import Bucket, Finding, check_observations

CATALOGS = load_catalogs(Path("src/span_panel_simulator/ebus_emitter/wire/catalogs"))


def _tree(node_type: str, properties: dict[str, object]) -> HomieTree:
    return build_tree(
        {
            "bess-1": {
                "name": "BESS",
                "type": "energy.ebus.device.bess",
                "children": [],
                "nodes": {"soc": {"name": "State", "type": node_type, "properties": properties}},
            }
        }
    )


def _rules(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


def test_o1_unregistered_ebus_capability_type() -> None:
    tree = _tree("energy.ebus.capability.nonesuch", {})
    findings = check_observations(tree, CATALOGS)
    assert _rules(findings, "O1")


def test_o2_widened_datatype_is_a_divergence_not_a_violation() -> None:
    tree = _tree(
        "energy.ebus.capability.soc",
        {"soc": {"name": "SoC", "datatype": "string", "unit": "%"}},
    )
    o2 = _rules(check_observations(tree, CATALOGS), "O2")
    assert o2 and o2[0].bucket is Bucket.DIVERGENCE


def test_o6_uncatalogued_property_is_an_extension() -> None:
    tree = _tree(
        "energy.ebus.capability.soc",
        {"span-extra": {"name": "Extra", "datatype": "float"}},
    )
    o6 = _rules(check_observations(tree, CATALOGS), "O6")
    assert o6 and o6[0].bucket is Bucket.EXTENSION


def test_o7_non_ebus_node_type_is_an_extension() -> None:
    findings = check_observations(_tree("vendor.custom.thing", {}), CATALOGS)
    assert _rules(findings, "O7")


def test_o9_unpublished_catalog_property_is_an_omission() -> None:
    tree = _tree(
        "energy.ebus.capability.soc",
        {"soc": {"name": "SoC", "datatype": "float", "unit": "%"}},
    )
    findings = check_observations(tree, CATALOGS)
    omitted = {f.property for f in _rules(findings, "O9")}
    assert {"soe", "total-energy-storage", "loadup-headroom"} <= omitted
