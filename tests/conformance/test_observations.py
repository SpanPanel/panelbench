from __future__ import annotations

import json

from panelbench.conformance.catalogs import load_catalogs
from panelbench.conformance.emitter_data import emitter_catalogs
from panelbench.conformance.model import HomieTree, build_tree
from panelbench.conformance.rules import Bucket, Finding, check_observations

CATALOGS = load_catalogs(emitter_catalogs())


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


def _circuit(node_id: str, node_type: str, properties: dict[str, object]) -> HomieTree:
    """A circuit device carrying one capability node.

    The `_tree` helper above hangs its node off a BESS, which is the wrong parent for
    `switch` and `load-shed`: both are circuit capabilities, and the settability rules
    under test are per-circuit commissioning locks.
    """
    return build_tree(
        {
            "circuit-1": {
                "name": "Refrigerator",
                "type": "energy.ebus.device.circuit",
                "children": [],
                "nodes": {node_id: {"name": node_id, "type": node_type, "properties": properties}},
            }
        }
    )


def _switch(relay: dict[str, object]) -> HomieTree:
    return _circuit("switch", "energy.ebus.capability.switch", {"relay": relay})


def test_o4_a_relay_locked_circuit_is_not_a_divergence() -> None:
    """`capabilities/switch.md:28` makes `relay` settable "when `relay-controllable`", so
    a locked circuit omitting `$settable` is the specification being followed. Every
    faithful clone of a real panel publishes this, and a report that flagged it would be
    reporting conformance."""
    tree = _switch({"name": "Circuit relay state", "datatype": "enum", "format": "OPEN,CLOSED"})

    assert not _rules(check_observations(tree, CATALOGS), "O4")


def test_o4_a_controllable_relay_declaring_settable_is_not_a_divergence_either() -> None:
    """The other half of the same panel. Both instances are conformant, which is exactly
    why the catalog's flat `settable: true` cannot decide this one."""
    tree = _switch(
        {
            "name": "Circuit relay state",
            "datatype": "enum",
            "format": "OPEN,CLOSED",
            "settable": True,
        }
    )

    assert not _rules(check_observations(tree, CATALOGS), "O4")


def test_o4_still_fires_on_an_unconditionally_settable_property_published_read_only() -> None:
    """`capabilities/load-shed.md:26` marks `priority` settable `yes`, unconditionally.
    Publishing it read-only is legal — the framework's conformance latitude, named at
    `load-shed.md:28` as "a permitted deviation" — and a permitted deviation is precisely
    what a divergence row records. The suppression above must not reach it."""
    tree = _circuit(
        "load-shed",
        "energy.ebus.capability.load-shed",
        {"priority": {"name": "Priority", "datatype": "enum", "format": "UNKNOWN,NEVER,OFF_GRID"}},
    )

    o4 = _rules(check_observations(tree, CATALOGS), "O4")
    assert o4 and o4[0].property == "priority" and o4[0].bucket is Bucket.DIVERGENCE


def test_o4_still_fires_when_a_read_only_property_is_declared_settable() -> None:
    """The suppression is one-directional. `relay-controllable` is `Settable: no`
    (`capabilities/switch.md:29`); a publisher offering to write it diverges whatever the
    sibling `relay` does."""
    tree = _circuit(
        "switch",
        "energy.ebus.capability.switch",
        {"relay-controllable": {"name": "Controllable", "datatype": "boolean", "settable": True}},
    )

    o4 = _rules(check_observations(tree, CATALOGS), "O4")
    assert o4 and o4[0].property == "relay-controllable"


def test_relay_is_still_conditionally_settable_in_the_vendored_catalog() -> None:
    """`_CONDITIONALLY_SETTABLE` restates a condition the catalog JSON carries only in
    prose, so a re-vendor that drops or reworks the clause must fail here rather than
    silently leaving the table asserting a rule the bytes no longer state."""
    catalog = json.loads((emitter_catalogs() / "switch.json").read_text())
    relay = catalog["properties"]["relay"]

    assert relay["settable"] is True
    assert "relay-controllable" in relay["description"]
    assert "Settable when" in relay["description"]
