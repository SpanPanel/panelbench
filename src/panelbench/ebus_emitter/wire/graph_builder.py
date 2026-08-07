"""Walk the manifest + mapping descriptors + profiles, build the ebus-sdk Device graph."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import ebus_sdk

from panelbench.ebus_emitter.exceptions import (
    ManifestValidationError,
    ProfileValidationError,
)
from panelbench.ebus_emitter.manifest import DeviceInstance, DeviceManifest
from panelbench.ebus_emitter.wire._sdk_seam import make_property
from panelbench.ebus_emitter.wire.mapping_loader import MappingDescriptor, MappingTable
from panelbench.ebus_emitter.wire.profile_loader import Profile, ProfileTable

PropertyKey = tuple[str, str, str]


@dataclass(slots=True)
class BuiltGraph:
    """Structural result of walking the manifest.

    ``devices`` maps instance id -> the SDK ``Device`` (root and descendants);
    ``properties`` maps ``(entity_class, instance_id, "cap/key")`` -> the SDK
    ``Property``, the seam the publisher and setter-wiring use; ``root_id`` is the
    single root device's instance id.

    No ``description_payloads`` and no ``node_types``: the SDK composes each device's
    Homie 5 ``$description`` itself — correctly scoped per device, with ``children`` /
    ``parent`` / ``root`` filled in from the tree — so ``LifecycleController`` reads
    ``device.description()`` rather than us assembling a parallel copy that could drift.
    """

    devices: dict[str, ebus_sdk.Device] = field(default_factory=dict)
    properties: dict[PropertyKey, ebus_sdk.Property] = field(default_factory=dict)
    root_id: str = ""


def build_graph(
    manifest: DeviceManifest,
    mapping: MappingTable,
    profiles: ProfileTable,
) -> BuiltGraph:
    graph = BuiltGraph()

    root_descriptors = [m for m in mapping.values() if m.placement.kind == "root-device"]
    if len(root_descriptors) != 1:
        raise ManifestValidationError(
            f"Expected exactly one root-device descriptor, got {len(root_descriptors)}"
        )
    root_class = root_descriptors[0].entity_class

    root_instances = manifest.of_class(root_class)
    if len(root_instances) != 1:
        raise ManifestValidationError(
            f"Expected exactly one {root_class!r} instance in manifest, got {len(root_instances)}"
        )
    root_instance = root_instances[0]

    # No ``mqtt_cfg``: the tree is transport-free by design. It composes ``$description``,
    # resolves ids and topics, and holds property values, but never opens a socket — this
    # emitter owns its transport and publishes through its own client.
    root_device = ebus_sdk.Device(
        root_instance.instance_id,
        name=root_instance.display_name,
        type=profiles[root_class].type,
    )
    graph.devices[root_instance.instance_id] = root_device
    graph.root_id = root_instance.instance_id

    _attach_profile(
        root_device,
        profiles[root_class],
        root_instance,
        graph,
        entity_class=root_class,
        parent_for_path=None,
        node_id_template=None,
    )

    # Topologically order non-root descriptors so that any descriptor whose
    # ``child-of-parent`` placement names a parent_entity_class is processed
    # AFTER that parent's descriptor. ``node-on-parent`` descriptors also
    # participate in the sort but their parent edge is already implicitly the
    # root device — they only become predecessors when something is parented
    # under them, which is not currently expressible (their target is always
    # the root). The DAG edges therefore come solely from
    # ``child-of-parent.parent_entity_class`` references.
    ordered = _topo_sort_descriptors(mapping, root_class)

    for descriptor in ordered:
        ec = descriptor.entity_class
        for inst in manifest.of_class(ec):
            if descriptor.placement.kind == "node-on-parent":
                _attach_profile(
                    root_device,
                    profiles[ec],
                    inst,
                    graph,
                    entity_class=ec,
                    parent_for_path=root_instance,
                    node_id_template=descriptor.placement.node_id_template,
                )
            elif descriptor.placement.kind == "child-of-parent":
                parent_ec = descriptor.placement.parent_entity_class
                if parent_ec is None:
                    raise ProfileValidationError(
                        f"mapping {ec!r} placement.kind='child-of-parent' requires "
                        "parent_entity_class to be set"
                    )

                # Resolve the parent SDK device. If parent_ec is the root, it's the
                # single root device; otherwise we must find the specific parent
                # instance built earlier by topo order.
                parent_instance: DeviceInstance
                if parent_ec == root_class:
                    parent_instance = root_instance
                else:
                    candidates = manifest.of_class(parent_ec)
                    if len(candidates) != 1:
                        raise ManifestValidationError(
                            f"Cannot place {ec!r} child instance {inst.instance_id!r}: "
                            f"expected exactly one {parent_ec!r} parent instance in "
                            f"manifest, got {len(candidates)}"
                        )
                    parent_instance = candidates[0]

                parent_device = graph.devices.get(parent_instance.instance_id)
                if parent_device is None:
                    raise ManifestValidationError(
                        f"Cannot place {ec!r} child instance {inst.instance_id!r}: "
                        f"parent device {parent_instance.instance_id!r} not yet built "
                        f"(topology bug — should have been ordered before this descriptor)"
                    )

                # The SDK parents at construction via ``parent=<Device>``: it appends the
                # child to the parent's children and derives root/parent itself. A child
                # never takes ``mqtt_cfg`` — it shares the root's, which here is absent by
                # design, and a transport-free root accepts children.
                child = ebus_sdk.Device(
                    inst.instance_id,
                    name=inst.display_name,
                    type=profiles[ec].type,
                    parent=parent_device,
                )
                graph.devices[inst.instance_id] = child
                _attach_profile(
                    child,
                    profiles[ec],
                    inst,
                    graph,
                    entity_class=ec,
                    parent_for_path=None,
                    node_id_template=descriptor.placement.node_id_template,
                )

    return graph


def _topo_sort_descriptors(mapping: MappingTable, root_class: str) -> list[MappingDescriptor]:
    """Return non-root mapping descriptors in topological order.

    Edges are derived from ``child-of-parent.parent_entity_class``: a child
    descriptor depends on its parent descriptor and must therefore be processed
    after it. Within a topological level, descriptors are tie-broken by
    ``placement.kind`` (``node-on-parent`` before ``child-of-parent``) and then
    by ``entity_class`` for determinism.

    Raises ``ProfileValidationError`` on cycles."""
    nodes: dict[str, MappingDescriptor] = {
        ec: m for ec, m in mapping.items() if m.placement.kind != "root-device"
    }

    # Build adjacency: edge parent_ec -> child_ec when child has parent_ec set
    # and parent_ec is itself a non-root descriptor (root parent contributes
    # no edge — the root device is built unconditionally first).
    in_degree: dict[str, int] = {ec: 0 for ec in nodes}
    successors: dict[str, list[str]] = {ec: [] for ec in nodes}
    for ec, descriptor in nodes.items():
        parent_ec = descriptor.placement.parent_entity_class
        if (
            descriptor.placement.kind == "child-of-parent"
            and parent_ec is not None
            and parent_ec != root_class
            and parent_ec in nodes
        ):
            successors[parent_ec].append(ec)
            in_degree[ec] += 1

    def _kind_rank(ec: str) -> int:
        return 0 if nodes[ec].placement.kind == "node-on-parent" else 1

    ready: deque[str] = deque(
        sorted(
            (ec for ec, deg in in_degree.items() if deg == 0),
            key=lambda ec: (_kind_rank(ec), ec),
        )
    )
    ordered: list[MappingDescriptor] = []
    while ready:
        ec = ready.popleft()
        ordered.append(nodes[ec])
        newly_ready: list[str] = []
        for succ in successors[ec]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                newly_ready.append(succ)
        for succ in sorted(newly_ready, key=lambda e: (_kind_rank(e), e)):
            ready.append(succ)

    if len(ordered) != len(nodes):
        unresolved = sorted(ec for ec, deg in in_degree.items() if deg > 0)
        raise ProfileValidationError(
            "cycle detected in mapping descriptor parent_entity_class graph involving: "
            + ", ".join(unresolved)
        )
    return ordered


def _attach_profile(
    device: ebus_sdk.Device,
    profile: Profile,
    instance: DeviceInstance,
    graph: BuiltGraph,
    *,
    entity_class: str,
    parent_for_path: DeviceInstance | None,
    node_id_template: str | None,
) -> None:
    """Attach the profile's capabilities + properties to the given device.

    For a device carrying its own profile (parent_for_path is None — the root, and every
    ``child-of-parent`` device) capability nodes use plain capability names, which is what
    makes a child's topic read ``{child_id}/{capability}/{property}``. For node-on-parent
    entities, capability nodes are namespaced with the instance ID so multiple
    circuits/lugs/etc. coexist on the parent without collision.

    All additions run inside one ``state_transition()`` so the SDK coalesces its
    description bookkeeping into a single init->ready cycle rather than flapping per
    property.
    """
    single_capability = len(profile.capabilities) == 1
    with device.state_transition():
        for cap_name, cap in profile.capabilities.items():
            if parent_for_path is None:
                node_id = cap_name
            else:
                node_prefix = _render_node_id(node_id_template or "{instance_id}", instance)
                node_id = node_prefix if single_capability else f"{node_prefix}-{cap_name}"
            node = device.add_node_from_dict(
                {
                    "id": node_id,
                    "name": cap_name,
                    "type": cap.type,
                }
            )
            for prop_key, prop in cap.properties.items():
                sdk_prop = make_property(
                    node=node,
                    key=prop_key,
                    name=prop.name,
                    datatype=_to_sdk_datatype(prop.datatype),
                    unit=_to_sdk_unit(prop.unit),
                    format_str=prop.format,
                    settable=prop.settable,
                )
                graph.properties[
                    (entity_class, instance.instance_id, f"{cap_name}/{prop_key}")
                ] = sdk_prop


def _render_node_id(template: str, instance: DeviceInstance) -> str:
    return template.format(
        instance_id=instance.instance_id,
        instance_id_short=instance.instance_id[:8],
        display_name=instance.display_name,
    )


def _to_sdk_datatype(dt: str) -> ebus_sdk.PropertyDatatype:
    mapping = {
        "string": ebus_sdk.PropertyDatatype.STRING,
        "integer": ebus_sdk.PropertyDatatype.INTEGER,
        "float": ebus_sdk.PropertyDatatype.FLOAT,
        "boolean": ebus_sdk.PropertyDatatype.BOOLEAN,
        "enum": ebus_sdk.PropertyDatatype.ENUM,
        "json": getattr(ebus_sdk.PropertyDatatype, "JSON", ebus_sdk.PropertyDatatype.STRING),
    }
    return mapping.get(dt.lower(), ebus_sdk.PropertyDatatype.STRING)


def _to_sdk_unit(unit: str | None) -> ebus_sdk.Unit | str | None:
    """Map a Homie unit string to an ``ebus_sdk.Unit``, or pass it through unchanged.

    ``Unit`` is a str-enum whose *value* is the Homie wire string (``Unit.VOLTS`` is
    ``"V"``, ``Unit.MINUTES`` is ``"min"``), so resolve by value. Correct by
    construction for every unit the SDK models, and it cannot silently drop one the way
    the previous hand-maintained name table did — that table mapped ``"V"`` to a
    non-existent ``VOLT`` member, so every voltage property published with no unit at
    all, and it had no entry for ``"min"``.

    A unit the enum does not model is returned **as the original string** rather than
    dropped. Homie's unit is explicitly free-form ("you are not limited to the recommended
    values"), and the SDK agrees — it accepts and publishes a plain string unit verbatim.
    ``Unit`` is a convenience vocabulary, not a constraint, so treating a missing member as
    "no unit" was our error, not a limit the SDK imposes. It was reachable: the spec's own
    ``breaker`` catalog uses ``kA`` for ``interrupting-rating``, which the enum lacks, so
    composing that property would have published it with no unit at all.

    This is deliberately *not* an escape hatch for the abstract unit tokens. Those name a
    dimension rather than a unit and must never reach the wire; ``profile_loader``
    rejects one at load time, well before this function sees it.
    """
    if unit is None:
        return None
    try:
        return ebus_sdk.Unit(unit)
    except ValueError:
        return unit
