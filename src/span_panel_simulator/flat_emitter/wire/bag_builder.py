"""Profile-driven snapshot → ``PropertyBag`` translator.

The wire-layer graph + mapping table together declare every property the
emitter can publish. The bag builder walks that declared set and pulls each
value out of an ``EbusPanelSnapshot`` using a static lookup table that maps
``(entity_class, capability/property)`` → snapshot accessor.

Two-purpose design:

1. **Mechanical and complete.** Every profile-declared property is considered
   on every tick; if its snapshot value is non-None, it goes into the bag.
   This eliminates the silent-drop bug where the old hand-rolled
   ``_snapshot_to_bag`` published only the ~25% of properties someone happened
   to remember.
2. **Fail loud on schema drift.** If the profile declares a property the
   builder has no source for, construction raises ``EmitterStateError``.
   Adding a new profile property without a corresponding snapshot field is a
   loud build failure rather than a silent missing topic.

Property values that resolve to ``None`` are skipped (Homie 5 allows missing
properties — the property's retained topic just isn't updated this tick)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from span_panel_simulator.flat_emitter.exceptions import EmitterStateError
from span_panel_simulator.flat_emitter.snapshot import EbusPanelSnapshot
from span_panel_simulator.flat_emitter.wire.graph_builder import BuiltGraph
from span_panel_simulator.flat_emitter.wire.mapping_loader import MappingTable
from span_panel_simulator.flat_emitter.wire.profile_loader import ProfileTable
from span_panel_simulator.flat_emitter.wire.property_bag import PropertyBag

# A ``Resolver`` is a function that pulls a single property's value from the
# snapshot. It receives the snapshot and the per-instance id and returns the
# value (or ``None`` to skip publication this tick). Resolvers are pure
# functions over the snapshot; no side effects.
Resolver = Callable[[EbusPanelSnapshot, str], object]


def _panel_resolver(getter: Callable[[EbusPanelSnapshot], object]) -> Resolver:
    """Wrap a panel-level getter so it ignores the per-instance id."""

    def _resolve(snapshot: EbusPanelSnapshot, _instance_id: str) -> object:
        return getter(snapshot)

    return _resolve


# ---------------------------------------------------------------------------
# Static resolver table — one entry per profile-declared property.
#
# Adding a new profile property: add it here AND add a snapshot field. The
# constructor's coverage check raises if you only do one half.
# ---------------------------------------------------------------------------


def _circuit_field(field: str) -> Resolver:
    def _resolve(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
        circuit = snapshot.circuits.get(instance_id)
        if circuit is None:
            return None
        return getattr(circuit, field)

    return _resolve


def _bess_field(field: str) -> Resolver:
    def _resolve(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
        bess = snapshot.battery.get(instance_id)
        if bess is None:
            return None
        return getattr(bess, field)

    return _resolve


def _pv_field(field: str) -> Resolver:
    def _resolve(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
        pv = snapshot.pv.get(instance_id)
        if pv is None:
            return None
        return getattr(pv, field)

    return _resolve


def _evse_field(field: str) -> Resolver:
    def _resolve(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
        evse = snapshot.evse.get(instance_id)
        if evse is None:
            return None
        return getattr(evse, field)

    return _resolve


def _lugs_field(field: str) -> Resolver:
    def _resolve(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
        lugs = snapshot.lugs.get(instance_id)
        if lugs is None:
            return None
        return getattr(lugs, field)

    return _resolve


def _circuit_space(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    circuit = snapshot.circuits.get(instance_id)
    if circuit is None or not circuit.tabs:
        return None
    return circuit.tabs[0]


def _circuit_breaker(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    circuit = snapshot.circuits.get(instance_id)
    if circuit is None or circuit.breaker_rating_a is None:
        return None
    return int(circuit.breaker_rating_a)


def _circuit_shed_priority(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    circuit = snapshot.circuits.get(instance_id)
    if circuit is None:
        return None
    if circuit.priority in ("OFF_GRID", "SOC_THRESHOLD", "NEVER"):
        return circuit.priority
    return "UNKNOWN"


def _circuit_relay_requester(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    circuit = snapshot.circuits.get(instance_id)
    if circuit is None:
        return None
    return "NONE" if circuit.relay_requester == "UNKNOWN" else circuit.relay_requester


def _circuit_wire_active_power(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    """Circuit ``active-power`` in the enclosure reference frame.

    The snapshot's ``instant_power_w`` is device-frame (positive = the circuit is
    consuming). The wire is enclosure-frame: positive means power flowing *into*
    the enclosure busbar (a circuit backfeeding, e.g. a PV inverter), negative
    means power flowing *out* of the busbar to a load. Hence the negation.

    The energy accumulators below must be relabelled for the same reason — see
    ``_circuit_wire_imported_energy``."""
    circuit = snapshot.circuits.get(instance_id)
    if circuit is None:
        return None
    return 0.0 if circuit.instant_power_w == 0 else -circuit.instant_power_w


def _circuit_wire_imported_energy(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    """Circuit ``imported-energy`` — energy imported *by the enclosure* from the
    circuit, i.e. the circuit backfeeding the busbar. That is the snapshot's
    ``produced_energy_wh``."""
    circuit = snapshot.circuits.get(instance_id)
    if circuit is None:
        return None
    return circuit.produced_energy_wh


def _circuit_wire_exported_energy(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    """Circuit ``exported-energy`` — energy exported *by the enclosure* to the
    circuit, i.e. normal load consumption. That is the snapshot's
    ``consumed_energy_wh``."""
    circuit = snapshot.circuits.get(instance_id)
    if circuit is None:
        return None
    return circuit.consumed_energy_wh


def _upper_lugs_direction(snapshot: EbusPanelSnapshot, instance_id: str) -> object:
    lugs = snapshot.lugs.get(instance_id)
    if lugs is None:
        return None
    return lugs.direction.upper()


# Wire property path ``"<capability>/<property-key>"`` → resolver.
# Keyed by (entity_class, "<cap>/<prop>"). One entry per profile-declared
# property; the constructor verifies coverage at startup.
_RESOLVERS: dict[tuple[str, str], Resolver] = {
    # ---- panel ----------------------------------------------------------
    ("panel", "core/vendor-name"): _panel_resolver(lambda s: s.info.vendor_name),
    ("panel", "core/model"): _panel_resolver(lambda s: s.info.panel_model),
    ("panel", "core/serial-number"): _panel_resolver(lambda s: s.info.serial_number),
    ("panel", "core/hardware-version"): _panel_resolver(lambda s: s.info.hardware_version),
    ("panel", "core/software-version"): _panel_resolver(
        lambda s: s.info.firmware_version,
    ),
    ("panel", "core/door"): _panel_resolver(lambda s: s.door.state),
    ("panel", "core/grid-islandable"): _panel_resolver(lambda s: s.pcs.grid_islandable),
    ("panel", "core/dominant-power-source"): _panel_resolver(
        lambda s: s.pcs.dominant_power_source,
    ),
    ("panel", "core/relay"): _panel_resolver(lambda s: s.status.main_relay_state),
    ("panel", "core/l1-voltage"): _panel_resolver(lambda s: s.meter.l1_voltage),
    ("panel", "core/l2-voltage"): _panel_resolver(lambda s: s.meter.l2_voltage),
    ("panel", "core/breaker-rating"): _panel_resolver(lambda s: s.pcs.main_breaker_rating_a),
    ("panel", "core/ethernet"): _panel_resolver(lambda s: s.status.eth0_link),
    ("panel", "core/wifi"): _panel_resolver(lambda s: s.status.wlan_link),
    ("panel", "core/wifi-ssid"): _panel_resolver(lambda s: s.status.wifi_ssid),
    ("panel", "core/vendor-cloud"): _panel_resolver(lambda s: s.status.cloud_connection),
    ("panel", "core/postal-code"): _panel_resolver(lambda s: s.status.postal_code),
    ("panel", "core/time-zone"): _panel_resolver(lambda s: s.status.time_zone),
    ("panel", "pcs/enabled"): _panel_resolver(lambda s: s.pcs.enabled),
    ("panel", "pcs/active"): _panel_resolver(lambda s: s.pcs.active),
    ("panel", "pcs/import-limit"): _panel_resolver(lambda s: s.pcs.import_limit_a),
    ("panel", "pcs/feed-import-limit"): _panel_resolver(lambda s: s.pcs.feed_import_limit_a),
    ("panel", "pcs/feed-import-limit-enablement"): _panel_resolver(
        lambda s: s.pcs.feed_import_limit_enablement,
    ),
    ("panel", "pcs/feed-import-limit-active"): _panel_resolver(
        lambda s: s.pcs.feed_import_limit_active,
    ),
    ("panel", "pcs/grid-import-limit"): _panel_resolver(lambda s: s.pcs.grid_import_limit_a),
    ("panel", "pcs/grid-import-limit-enablement"): _panel_resolver(
        lambda s: s.pcs.grid_import_limit_enablement,
    ),
    ("panel", "pcs/grid-import-limit-active"): _panel_resolver(
        lambda s: s.pcs.grid_import_limit_active,
    ),
    ("panel", "pcs/off-grid-import-limit"): _panel_resolver(
        lambda s: s.pcs.off_grid_import_limit_a,
    ),
    ("panel", "pcs/off-grid-import-limit-enablement"): _panel_resolver(
        lambda s: s.pcs.off_grid_import_limit_enablement,
    ),
    ("panel", "pcs/off-grid-import-limit-active"): _panel_resolver(
        lambda s: s.pcs.off_grid_import_limit_active,
    ),
    ("panel", "pcs/requested-import-limit"): _panel_resolver(
        lambda s: s.pcs.requested_import_limit_a,
    ),
    ("panel", "pcs/requested-import-limit-enablement"): _panel_resolver(
        lambda s: s.pcs.requested_import_limit_enablement,
    ),
    ("panel", "pcs/requested-import-limit-active"): _panel_resolver(
        lambda s: s.pcs.requested_import_limit_active,
    ),
    ("panel", "power-flows/pv"): _panel_resolver(lambda s: s.power_flows.pv),
    ("panel", "power-flows/battery"): _panel_resolver(lambda s: s.power_flows.battery),
    ("panel", "power-flows/grid"): _panel_resolver(lambda s: s.power_flows.grid),
    ("panel", "power-flows/site"): _panel_resolver(lambda s: s.power_flows.site),
    ("panel", "meter/active-power"): _panel_resolver(
        lambda s: s.meter.instant_grid_power_w,
    ),
    # ---- circuit --------------------------------------------------------
    ("circuit", "circuit/name"): _circuit_field("name"),
    ("circuit", "circuit/relay"): _circuit_field("relay_state"),
    ("circuit", "circuit/relay-requester"): _circuit_relay_requester,
    ("circuit", "circuit/breaker-rating"): _circuit_breaker,
    ("circuit", "circuit/current"): _circuit_field("current_a"),
    ("circuit", "circuit/active-power"): _circuit_wire_active_power,
    ("circuit", "circuit/imported-energy"): _circuit_wire_imported_energy,
    ("circuit", "circuit/exported-energy"): _circuit_wire_exported_energy,
    ("circuit", "circuit/space"): _circuit_space,
    ("circuit", "circuit/dipole"): _circuit_field("is_240v"),
    ("circuit", "circuit/shed-priority"): _circuit_shed_priority,
    ("circuit", "circuit/pcs-managed"): _circuit_field("pcs_managed"),
    ("circuit", "circuit/pcs-priority"): _circuit_field("pcs_priority"),
    ("circuit", "circuit/sheddable"): _circuit_field("is_sheddable"),
    ("circuit", "circuit/never-backup"): _circuit_field("is_never_backup"),
    ("circuit", "circuit/always-on"): _circuit_field("always_on"),
    # ---- bess -----------------------------------------------------------
    ("bess", "bess/vendor-name"): _bess_field("vendor_name"),
    ("bess", "bess/product-name"): _bess_field("product_name"),
    ("bess", "bess/model"): _bess_field("model"),
    ("bess", "bess/serial-number"): _bess_field("serial_number"),
    ("bess", "bess/software-version"): _bess_field("firmware_version"),
    ("bess", "bess/nameplate-capacity"): _bess_field("nameplate_capacity_kwh"),
    ("bess", "bess/relative-position"): _bess_field("relative_position"),
    ("bess", "bess/feed"): _bess_field("feed_circuit_id"),
    ("bess", "bess/soc"): _bess_field("soe_percentage"),
    ("bess", "bess/soe"): _bess_field("soe_kwh"),
    ("bess", "bess/connected"): _bess_field("connected"),
    ("bess", "bess/grid-state"): _bess_field("grid_state"),
    # ---- pv -------------------------------------------------------------
    ("pv", "pv/vendor-name"): _pv_field("vendor_name"),
    ("pv", "pv/product-name"): _pv_field("product_name"),
    ("pv", "pv/serial-number"): _pv_field("serial_number"),
    ("pv", "pv/software-version"): _pv_field("firmware_version"),
    ("pv", "pv/nameplate-capacity"): _pv_field("nameplate_capacity_w"),
    ("pv", "pv/relative-position"): _pv_field("relative_position"),
    ("pv", "pv/feed"): _pv_field("feed_circuit_id"),
    # ---- evse -----------------------------------------------------------
    ("evse", "evse/vendor-name"): _evse_field("vendor_name"),
    ("evse", "evse/product-name"): _evse_field("product_name"),
    ("evse", "evse/part-number"): _evse_field("part_number"),
    ("evse", "evse/serial-number"): _evse_field("serial_number"),
    ("evse", "evse/software-version"): _evse_field("firmware_version"),
    ("evse", "evse/feed"): _evse_field("feed_circuit_id"),
    ("evse", "evse/lock-state"): _evse_field("lock_state"),
    ("evse", "evse/status"): _evse_field("status"),
    ("evse", "evse/advertised-current"): _evse_field("advertised_current_a"),
    # ---- lugs -----------------------------------------------------------
    ("lugs", "lugs/direction"): _upper_lugs_direction,
    ("lugs", "lugs/feed"): _lugs_field("feed"),
    ("lugs", "lugs/active-power"): _lugs_field("active_power_w"),
    ("lugs", "lugs/l1-current"): _lugs_field("l1_current_a"),
    ("lugs", "lugs/l2-current"): _lugs_field("l2_current_a"),
    ("lugs", "lugs/imported-energy"): _lugs_field("imported_energy_wh"),
    ("lugs", "lugs/exported-energy"): _lugs_field("exported_energy_wh"),
}


# ---------------------------------------------------------------------------
# Bag builder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _BoundProperty:
    """Resolved property binding ready for per-tick evaluation."""

    entity_class: str
    instance_id: str
    property_path: str
    resolver: Resolver


class BagBuilder:
    """Walk the wire graph + profiles to populate a ``PropertyBag`` per tick.

    Construction validates that every profile-declared property has a
    resolver; this is the structural check that catches silent-drop drift
    between profile JSONs and the snapshot dataclasses."""

    def __init__(
        self,
        graph: BuiltGraph,
        mapping: MappingTable,
        profiles: ProfileTable,
    ) -> None:
        del mapping  # accepted for API symmetry; not consulted today.
        self._bound: list[_BoundProperty] = []

        # First pass: structural coverage check. Every profile property the
        # graph references must have a resolver entry. A missing resolver is a
        # programmer error (profile JSON updated without bag-builder follow-up)
        # and we want to fail at construction rather than silently drop topics.
        missing: list[tuple[str, str]] = []
        for ec, profile in profiles.items():
            for cap_name, cap in profile.capabilities.items():
                for prop_key in cap.properties:
                    full = f"{cap_name}/{prop_key}"
                    if (ec, full) not in _RESOLVERS:
                        missing.append((ec, full))
        if missing:
            joined = ", ".join(f"{ec}.{p}" for ec, p in missing)
            raise EmitterStateError(
                f"BagBuilder: profile-declared properties have no snapshot "
                f"resolver: {joined}. Snapshot dataclasses and wire profiles are "
                f"out of sync.",
            )

        # Second pass: bind resolvers to the (entity_class, instance_id,
        # property_path) keys actually present in the graph. The graph already
        # encodes which instances exist for each entity_class.
        for entity_class, instance_id, property_path in graph.properties:
            resolver = _RESOLVERS[(entity_class, property_path)]
            self._bound.append(
                _BoundProperty(
                    entity_class=entity_class,
                    instance_id=instance_id,
                    property_path=property_path,
                    resolver=resolver,
                ),
            )

    def build(self, snapshot: EbusPanelSnapshot) -> PropertyBag:
        """Pull a value for every bound property; skip ``None`` values
        (Homie 5 lets a property's retained topic stay unchanged when the
        emitter has nothing fresh to say)."""
        bag = PropertyBag(values={})
        for bound in self._bound:
            value = bound.resolver(snapshot, bound.instance_id)
            if value is None:
                continue
            bag.set(bound.entity_class, bound.instance_id, bound.property_path, value)
        return bag
