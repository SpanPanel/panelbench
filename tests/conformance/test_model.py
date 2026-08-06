from __future__ import annotations

import pytest

from span_panel_simulator.conformance.model import DescriptionError, build_tree, parse_device


def test_parse_device_reads_nodes_and_properties() -> None:
    raw = {
        "homie": "5.0",
        "version": 1,
        "type": "energy.ebus.device.bess",
        "name": "BESS",
        "nodes": {
            "soc": {
                "name": "State",
                "type": "energy.ebus.capability.soc",
                "properties": {
                    "soe": {"name": "State of energy", "datatype": "float", "unit": "kWh"}
                },
            }
        },
        "children": [],
        "extensions": [],
    }
    device = parse_device("bess-1", raw)
    assert device.type == "energy.ebus.device.bess"
    prop = device.nodes["soc"].properties["soe"]
    assert prop.datatype == "float"
    assert prop.unit == "kWh"
    assert prop.settable is False
    assert prop.retained is True
    assert prop.format is None


def test_missing_datatype_is_a_description_error() -> None:
    raw = {
        "name": "d",
        "type": "t",
        "nodes": {"n": {"name": "n", "type": "t", "properties": {"p": {"name": "p"}}}},
        "children": [],
    }
    with pytest.raises(DescriptionError, match="datatype"):
        parse_device("d", raw)


def test_build_tree_indexes_by_device_id() -> None:
    doc = {"name": "d", "type": "t", "nodes": {}, "children": []}
    tree = build_tree({"d1": doc, "d2": doc})
    assert sorted(tree.devices) == ["d1", "d2"]
