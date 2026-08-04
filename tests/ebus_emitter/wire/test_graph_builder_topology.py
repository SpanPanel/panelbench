"""Topology tests for ``build_graph`` — verify ``parent_entity_class`` is honoured
for ``child-of-parent`` placements (MID-inside-BESS readiness, etc.) using
synthetic mapping descriptors + placeholder profiles. Production mapping/profile
JSONs are NOT modified — fixtures live entirely in this test module."""

from __future__ import annotations

import pytest

from span_panel_simulator.flat_emitter.exceptions import ProfileValidationError
from span_panel_simulator.flat_emitter.manifest import DeviceInstance, DeviceManifest
from span_panel_simulator.flat_emitter.wire.graph_builder import build_graph
from span_panel_simulator.flat_emitter.wire.mapping_loader import (
    DiscoveryConfig,
    DisplayConfig,
    MappingDescriptor,
    MappingTable,
    Placement,
    WireConfig,
)
from span_panel_simulator.flat_emitter.wire.profile_loader import (
    Profile,
    ProfileCapability,
    ProfileProperty,
    ProfileTable,
)


def _minimal_profile(entity_class: str, type_str: str) -> Profile:
    """Single-property profile sufficient for graph construction."""
    return Profile(
        entity_class=entity_class,
        version=1,
        type=type_str,
        capabilities={
            "info": ProfileCapability(
                type="generic",
                properties={
                    "id": ProfileProperty(
                        name="ID",
                        datatype="string",
                        unit=None,
                        format=None,
                        settable=False,
                    )
                },
            )
        },
    )


def _descriptor(
    entity_class: str,
    *,
    placement: Placement,
    profile_filename: str,
) -> MappingDescriptor:
    return MappingDescriptor(
        entity_class=entity_class,
        profile=profile_filename,
        profile_version=1,
        placement=placement,
        wire=WireConfig(
            device_id_source="self",
            property_path_template="{capability}/{property_key}",
        ),
        display=DisplayConfig(
            name_template="{display_name}",
            fallback_name_template=f"{entity_class} {{instance_id_short}}",
        ),
        discovery=DiscoveryConfig(
            description_owner="self",
            state_owner="self",
        ),
    )


def _three_level_chain() -> tuple[DeviceManifest, MappingTable, ProfileTable]:
    """Synthetic panel -> bess -> mid topology.

    panel: root device.
    bess:  child-of-parent (parent_entity_class=panel).
    mid:   child-of-parent (parent_entity_class=bess) — the MID-inside-BESS
           topology that the upcoming eBus migration requires.
    """
    mapping = MappingTable()
    mapping["panel"] = _descriptor(
        "panel",
        placement=Placement(kind="root-device", device_id_template="{instance_id}"),
        profile_filename="panel.json",
    )
    mapping["bess"] = _descriptor(
        "bess",
        placement=Placement(
            kind="child-of-parent",
            parent_entity_class="panel",
            device_id_template="{instance_id}",
        ),
        profile_filename="bess.json",
    )
    mapping["mid"] = _descriptor(
        "mid",
        placement=Placement(
            kind="child-of-parent",
            parent_entity_class="bess",
            device_id_template="{instance_id}",
        ),
        profile_filename="mid.json",
    )

    profiles = ProfileTable()
    profiles["panel"] = _minimal_profile("panel", "ebus.panel")
    profiles["bess"] = _minimal_profile("bess", "ebus.bess")
    profiles["mid"] = _minimal_profile("mid", "ebus.mid")

    manifest = DeviceManifest(
        instances=(
            DeviceInstance(entity_class="panel", instance_id="p1", display_name="Span"),
            DeviceInstance(entity_class="bess", instance_id="b1", display_name="Powerwall"),
            DeviceInstance(entity_class="mid", instance_id="m1", display_name="MID"),
        )
    )
    return manifest, mapping, profiles


def test_three_level_chain_parents_mid_under_bess() -> None:
    manifest, mapping, profiles = _three_level_chain()
    g = build_graph(manifest, mapping, profiles)

    # All three devices were created.
    assert set(g.devices.keys()) == {"p1", "b1", "m1"}

    # children_of records the parent->children topology.
    assert g.children_of["p1"] == ("b1",)
    assert g.children_of["b1"] == ("m1",)
    assert "m1" not in g.children_of  # leaf

    # MID was parented under BESS, not under the root panel.
    mid_device = g.devices["m1"]
    bess_device = g.devices["b1"]
    assert mid_device.parent_id() == "b1"
    assert mid_device.root_id() == "p1"
    assert bess_device.parent_id() == "p1"
    assert bess_device.root_id() == "p1"

    # SDK child registration.
    assert "m1" in bess_device.children_ids()
    assert "b1" in g.devices["p1"].children_ids()


def test_descriptor_order_does_not_affect_result() -> None:
    """Topo sort must process bess before mid even if mapping order is reversed."""
    manifest, mapping, profiles = _three_level_chain()

    # Reverse insertion order: mid first, then bess, then panel.
    reordered = MappingTable()
    reordered["mid"] = mapping["mid"]
    reordered["bess"] = mapping["bess"]
    reordered["panel"] = mapping["panel"]

    g = build_graph(manifest, reordered, profiles)
    assert g.children_of["b1"] == ("m1",)
    assert g.devices["m1"].parent_id() == "b1"


def test_cycle_in_parent_entity_class_raises() -> None:
    """Two non-root descriptors that name each other as parent must raise."""
    profiles = ProfileTable()
    profiles["panel"] = _minimal_profile("panel", "ebus.panel")
    profiles["a"] = _minimal_profile("a", "ebus.a")
    profiles["b"] = _minimal_profile("b", "ebus.b")

    mapping = MappingTable()
    mapping["panel"] = _descriptor(
        "panel",
        placement=Placement(kind="root-device", device_id_template="{instance_id}"),
        profile_filename="panel.json",
    )
    mapping["a"] = _descriptor(
        "a",
        placement=Placement(kind="child-of-parent", parent_entity_class="b"),
        profile_filename="a.json",
    )
    mapping["b"] = _descriptor(
        "b",
        placement=Placement(kind="child-of-parent", parent_entity_class="a"),
        profile_filename="b.json",
    )

    manifest = DeviceManifest(
        instances=(
            DeviceInstance(entity_class="panel", instance_id="p1", display_name="Span"),
            DeviceInstance(entity_class="a", instance_id="a1", display_name="A"),
            DeviceInstance(entity_class="b", instance_id="b1", display_name="B"),
        )
    )

    with pytest.raises(ProfileValidationError, match="cycle"):
        build_graph(manifest, mapping, profiles)
