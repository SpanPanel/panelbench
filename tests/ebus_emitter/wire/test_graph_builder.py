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
    profiles = load_profiles()
    mapping = load_mapping_table()
    g = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    # Panel is the only Device under v1_flat node-on-parent layout.
    assert "p1" in g.devices
    assert "c1" not in g.devices
    # Circuit's properties are present, attached to the panel device under namespaced nodes.
    assert ("circuit", "c1", "circuit/active-power") in g.properties
    assert ("circuit", "c1", "circuit/relay") in g.properties


def test_build_graph_is_deterministic() -> None:
    profiles = load_profiles()
    mapping = load_mapping_table()
    g1 = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    g2 = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    assert sorted(g1.properties.keys()) == sorted(g2.properties.keys())
    assert g1.description_payloads == g2.description_payloads


def test_build_graph_includes_panel_settable_property() -> None:
    profiles = load_profiles()
    mapping = load_mapping_table()
    g = build_graph(_manifest_panel_with_one_circuit(), mapping, profiles)
    assert ("panel", "p1", "core/dominant-power-source") in g.properties
