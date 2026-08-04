from span_panel_simulator.ebus_emitter.manifest import DeviceInstance, DeviceManifest
from span_panel_simulator.ebus_emitter.wire.graph_builder import build_graph
from span_panel_simulator.ebus_emitter.wire.mapping_loader import load_mapping_table
from span_panel_simulator.ebus_emitter.wire.profile_loader import load_profiles


def _manifest_panel_with_one_circuit() -> DeviceManifest:
    return DeviceManifest(
        instances=(
            DeviceInstance(entity_class="panel", instance_id="p1", display_name="Span"),
            DeviceInstance(entity_class="circuit", instance_id="c1", display_name="Kitchen"),
        )
    )


def test_build_graph_for_panel_and_one_circuit() -> None:
    """Under parent/child a circuit is its own Device, not a node on the panel.

    This is the inversion the schema change makes: the flat layout put every
    circuit's properties on the panel device under a namespaced node, so `c1` was
    deliberately absent from `devices`. It is now present, with the panel as parent.
    """
    profiles = load_profiles()
    mapping = load_mapping_table()
    g = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    assert "p1" in g.devices
    assert "c1" in g.devices
    assert g.root_id == "p1"
    assert g.devices["c1"].parent_id() == "p1"
    # Properties hang off the circuit's own device, under capability nodes.
    assert ("circuit", "c1", "meter/active-power") in g.properties
    assert ("circuit", "c1", "switch/relay") in g.properties


def test_build_graph_is_deterministic() -> None:
    profiles = load_profiles()
    mapping = load_mapping_table()
    g1 = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    g2 = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    assert sorted(g1.properties.keys()) == sorted(g2.properties.keys())
    assert sorted(g1.devices) == sorted(g2.devices)

    # `description_payloads` is gone: the SDK composes each device's $description
    # itself, correctly scoped, so there is no parallel copy of ours to compare.
    #
    # `version` is excluded deliberately — it is a millisecond timestamp, so two
    # builds agree only when they land in the same millisecond. Comparing it made
    # this test pass or fail on timing rather than on determinism.
    def _structure(payload: dict[str, object]) -> dict[str, object]:
        return {k: v for k, v in payload.items() if k != "version"}

    assert _structure(g1.devices["p1"].description()) == _structure(g2.devices["p1"].description())


def test_build_graph_includes_panel_settable_property() -> None:
    profiles = load_profiles()
    mapping = load_mapping_table()
    g = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    # The flat `core/dominant-power-source` split in two; the settable half is here.
    assert ("panel", "p1", "shed/asserted-islanding-state") in g.properties
