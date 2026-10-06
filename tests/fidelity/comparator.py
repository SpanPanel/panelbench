"""Structural comparison of two producers over one panel.

Conformance asks whether everything a producer publishes is legal, and the
specification permits omission — so a producer that publishes almost nothing is
perfectly conformant. This asks the other question: does panelbench publish what
the reference producer publishes?

Three comparisons live here, and they answer different questions.

``compare`` is structural: which devices exist, and which properties each
declares. Numeric payloads and timestamps vary by design between the two and are
never grounds for a failure.

``compare_identity_values`` compares the *payloads* of the ``info`` node, and
only those. Structure alone cannot see a device whose identity is wrong rather
than absent — a MID publishing the wrong serial is byte-for-byte structurally
perfect. ``info`` is the right and only scope for this: it is a catalog-declared
node whose properties resolve from ``DeviceInstance.metadata``, so it is exactly
the surface a manifest builder decides. Every other node resolves from tick
physics, where divergence is expected.

``compare_settable`` measures the ``$settable`` declarations the commissioning
locks reach the wire as, which neither of the other two can see.

Devices are never aligned by instance id: the two producers derive ids
differently, so an id-keyed diff reports every device as a mismatch and nothing
useful. ``role_key`` aligns them instead. Most devices align by declared
``type`` and ``name``, and circuits also by the breaker spaces they occupy,
because a SPAN panel gives two commissioned PV circuits the same name. A
battery, its MID, an inverter and a SPAN Drive align by where they hang,
because SPAN firmware names such devices after their own device ids.

Neither producer can read the other's input any more — upstream's input is a
panel definition and PanelBench's is a behaviour config — so each cell gives
both producers the same *panel* by a different route. The example cell runs
upstream's shipped definition through upstream's emitter and through
PanelBench's import of it. The PanelBench cell runs PanelBench's own config and
lets upstream's capture read PanelBench's published tree back into a definition.
The captured cell runs a real panel's masked capture, shipped with the pinned
release, through upstream's emitter and through PanelBench's import of it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ebus_panel_sim import (
    Emitter,
    PanelDefinition,
    SetterRegistry,
    TickInputs,
    load_definition,
    load_ticks,
)
from ebus_panel_sim.capture import definition_from_tree, tree_from_retained, tree_from_snapshot

from panelbench.definition_import import config_from_definition
from panelbench.emitter_adapter.wire_capture import (
    RecordingTransport,
    as_capture,
    capture_retained,
)
from tests._helpers import DEFAULT_CONFIG, write_config

FIXTURES = Path(__file__).parent / "fixtures"
UPSTREAM = FIXTURES / "upstream"
REFERENCE_DEFINITION = UPSTREAM / "forty_tab_minimal.yaml"
REFERENCE_TICKS = UPSTREAM / "forty_tab_minimal.ticks.yaml"

PANELBENCH_CONFIG = DEFAULT_CONFIG
"""The tracked 40-tab template, PanelBench's own richest config: the one `tests._helpers` names."""

IDENTITY_NODE = "info"
"""The catalog node carrying manifest-derived identity, per ``wire/catalogs/info.json``.

Named rather than inlined because it is a claim about the specification: these
are the properties a *manifest builder* decides, which is what makes them
comparable across producers at all.
"""

Capture = dict[str, dict[str, str]]

_IDLE_TICK = TickInputs(current_time=0.0, grid_online=True, circuits={})


def publish_reference(definition: PanelDefinition, ticks: Sequence[TickInputs]) -> Capture:
    """Upstream's emitter publishing *definition* through *ticks*: the reference producer.

    Recorded with the same last-wins transport PanelBench's captures use, so both
    sides of a cell are seen the way a consumer replaying retained topics sees them.
    """
    transport = RecordingTransport()
    emitter = Emitter.from_definition(definition, SetterRegistry(), mqttc=transport)
    emitter.start()
    for tick in ticks:
        emitter.publish_tick(tick)
    return as_capture(transport.retained)


def reference_example() -> Capture:
    """Upstream's shipped example, published by upstream."""
    return publish_reference(load_definition(REFERENCE_DEFINITION), load_ticks(REFERENCE_TICKS))


async def panelbench_imported(definition: PanelDefinition, workdir: Path) -> Capture:
    """PanelBench running its own import of *definition*, as the dashboard's Import does."""
    path = write_config(workdir / "imported.yaml", config_from_definition(definition))
    return as_capture(await capture_retained(path))


async def panelbench_example(workdir: Path) -> Capture:
    """PanelBench running its own import of upstream's shipped example."""
    return await panelbench_imported(load_definition(REFERENCE_DEFINITION), workdir)


def reference_reading(retained: Mapping[str, bytes]) -> Capture:
    """Upstream's reading of a published tree, republished by upstream's emitter."""
    tree = tree_from_retained({topic: payload.decode() for topic, payload in retained.items()})
    definition, _notes = definition_from_tree(tree, variant="span", mask=False)
    return publish_reference(definition, (_IDLE_TICK,))


async def example_pair(workdir: Path) -> tuple[Capture, Capture]:
    """The example cell: (reference, PanelBench)."""
    return reference_example(), await panelbench_example(workdir)


async def panelbench_pair(workdir: Path) -> tuple[Capture, Capture]:
    """The PanelBench cell: (reference, PanelBench)."""
    del workdir
    retained = await capture_retained(PANELBENCH_CONFIG)
    return reference_reading(retained), as_capture(retained)


CAPTURED_PANEL = UPSTREAM / "main32_r202639-tree-v1.json"
"""The pinned release's masked capture of a real panel on SPAN release 202639 or later."""


def captured_definition(path: Path = CAPTURED_PANEL) -> PanelDefinition:
    """Upstream's definition of the captured panel, read by upstream's own readers.

    The ``tree-v1`` snapshot is mapped by ``definition_from_tree`` without masking
    again, because upstream masked it before shipping it. The variant is inferred,
    as upstream's capture infers it for a live panel.
    """
    tree = tree_from_snapshot(json.loads(path.read_text(encoding="utf-8")))
    definition, _notes = definition_from_tree(tree, mask=False)
    return definition


async def captured_pair(workdir: Path) -> tuple[Capture, Capture]:
    """The captured cell: (upstream publishing the captured panel, PanelBench's import of it)."""
    definition = captured_definition()
    return publish_reference(definition, (_IDLE_TICK,)), await panelbench_imported(
        definition, workdir
    )


def role_of(device_id: str, properties: dict[str, str]) -> str:
    """A device's declared ``type::name``.

    Stable across producers for the devices both name the same way: the panel, its
    lugs, and a circuit, whose name is the one a user gave it. Not for a battery,
    its MID, an inverter or a SPAN Drive, which SPAN firmware names after their own
    device ids (``role_key``).

    Falls back to the raw id when a device published no parsable
    ``$description``, which is itself worth surfacing as a mismatch rather than
    hiding behind a shared placeholder.
    """
    description = properties.get("$description")
    if not description:
        return f"<no-description>::{device_id}"
    try:
        parsed = json.loads(description)
    except json.JSONDecodeError:
        return f"<unparsable-description>::{device_id}"
    return f"{parsed.get('type', '?')}::{parsed.get('name', device_id)}"


_KEYED_BY_FEED = frozenset({"bess", "evse", "pv"})
"""Device classes keyed by what they hang from rather than by name."""


def role_key(device_id: str, devices: dict[str, dict[str, str]]) -> str:
    """A cross-producer key for *device_id*, one of *devices*.

    A circuit's key is its role plus the breaker spaces it occupies. A SPAN panel
    names every commissioned PV circuit "Commissioned PV System", so a role alone
    collapses two such circuits into one, and a producer that dropped either would
    still compare equal. The spaces are applied to every circuit, not only to
    repeated names, so a key does not depend on what else the capture holds. A
    circuit publishing no spaces falls back to its id, which surfaces as a mismatch
    rather than hiding one.

    A battery, its MID and an inverter are keyed by topology, never by name. SPAN
    firmware names each after its own device id (both public MAIN 32 captures,
    r202633 and r202639, do), and the two producers' ids differ by construction,
    so no name could align them. What both share is where the device hangs: an
    inverter or an in-panel battery by the spaces of the circuit that feeds it, a
    battery upstream of the panel by the direction of the lugs it feeds, and a MID
    by its battery's key. A device whose place cannot be resolved is refused rather
    than keyed by its name, which would only reintroduce the misalignment quietly.

    A SPAN Drive is keyed by the spaces of the circuit that feeds it, for the same
    reason. Neither public capture has one, but upstream's capture keeps a Drive's
    published name exactly as it keeps the others', so a firmware that names the
    Drive after its id, as it does every other device it proxies, would misalign it
    the same way.

    Every other device keeps its ``role_of``.
    """
    props = devices[device_id]
    device_class = class_of(props)
    if device_class == "circuit":
        return f"{role_of(device_id, props)} @{props.get('info/spaces', device_id)}"
    if device_class in _KEYED_BY_FEED:
        return f"{_declared(props, 'type')} @{_feed_of(device_id, devices)}"
    if device_class == "mid":
        return f"{_declared(props, 'type')} of {_battery_key(device_id, devices)}"
    return role_of(device_id, props)


def _declared(body: dict[str, str], field_name: str) -> str | None:
    """The ``$description``'s *field_name* as a string, or None when it has none."""
    description = body.get("$description")
    if not description:
        return None
    try:
        value = json.loads(description).get(field_name)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, str) else None


def _feed_of(device_id: str, devices: dict[str, dict[str, str]]) -> str:
    """Where *device_id* hangs: its feeding circuit's spaces, or ``<direction> lugs``.

    A circuit names the device it feeds in ``connection/feeds-device-id``; lugs
    fed by an upstream battery name it in ``connection/fed-by-device-id``. Exactly
    one of them must name the device.
    """
    places: list[str] = []
    for other_id, body in devices.items():
        other_class = class_of(body)
        if other_class == "circuit" and body.get("connection/feeds-device-id") == device_id:
            spaces = body.get("info/spaces")
            if spaces is None:
                raise ValueError(
                    f"circuit {other_id!r} feeds {device_id!r} but publishes no info/spaces"
                )
            places.append(spaces)
        elif other_class == "lugs" and body.get("connection/fed-by-device-id") == device_id:
            direction = body.get("info/direction")
            if direction is None:
                raise ValueError(
                    f"lugs {other_id!r} are fed by {device_id!r} but publish no info/direction"
                )
            places.append(f"{direction} lugs")
    if len(places) != 1:
        raise ValueError(
            f"{device_id!r} hangs from {len(places)} circuits or lugs, not one, so it cannot "
            "be keyed by topology"
        )
    return places[0]


def _battery_key(device_id: str, devices: dict[str, dict[str, str]]) -> str:
    """The key of the battery a MID belongs to, which ``$description.parent`` names."""
    parent = _declared(devices[device_id], "parent")
    if parent is None or class_of(devices.get(parent, {})) != "bess":
        raise ValueError(
            f"MID {device_id!r} names no published battery as its parent, so it cannot be "
            "keyed by topology"
        )
    return role_key(parent, devices)


def keyed_devices(devices: dict[str, dict[str, str]]) -> dict[str, tuple[str, dict[str, str]]]:
    """Each device's id and properties, keyed by ``role_key``.

    Any repeated key is refused: merging two devices would hide one of them. Every
    instrument that keys by role goes through here, so none can merge them quietly.
    """
    keyed: dict[str, tuple[str, dict[str, str]]] = {}
    for device_id, props in devices.items():
        key = role_key(device_id, devices)
        if key in keyed:
            raise ValueError(f"two devices share the role {key!r}; comparing would merge them")
        keyed[key] = (device_id, props)
    return keyed


def by_role(devices: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Devices' properties keyed by ``role_key``, refusing a repeat as ``keyed_devices`` does."""
    return {key: props for key, (_device_id, props) in keyed_devices(devices).items()}


def declared_properties(body: dict[str, str]) -> set[str]:
    """``node/property`` keys a device announces in its ``$description``."""
    description = body.get("$description")
    if not description:
        return set()
    try:
        nodes = json.loads(description).get("nodes") or {}
    except json.JSONDecodeError:
        return set()
    return {
        f"{node_id}/{prop}"
        for node_id, node in nodes.items()
        for prop in (node.get("properties") or {})
    }


def class_of(body: dict[str, str]) -> str:
    """The trailing segment of the declared device type, e.g. ``lugs``, ``mid``."""
    description = body.get("$description")
    if not description:
        return "?"
    try:
        return str(json.loads(description).get("type", "?")).rsplit(".", 1)[-1]
    except json.JSONDecodeError:
        return "?"


@dataclass(frozen=True)
class ParityReport:
    """What panelbench fails to publish, and what it publishes beyond the reference."""

    missing_devices: dict[str, int] = field(default_factory=dict)
    """role -> number of topics the reference publishes for it."""

    extra_devices: dict[str, int] = field(default_factory=dict)

    missing_properties: dict[str, list[str]] = field(default_factory=dict)
    """role -> property keys present in the reference and absent here."""

    extra_properties: dict[str, list[str]] = field(default_factory=dict)

    @property
    def missing_topic_count(self) -> int:
        return sum(self.missing_devices.values()) + sum(
            len(v) for v in self.missing_properties.values()
        )

    def as_baseline(self) -> dict[str, Any]:
        """The JSON-comparable form, compared with an empty report or a cell's baseline."""
        return {
            "missing_devices": self.missing_devices,
            "extra_devices": self.extra_devices,
            "missing_properties": self.missing_properties,
            "extra_properties": self.extra_properties,
        }

    def describe(self) -> str:
        lines: list[str] = []
        for role, count in sorted(self.missing_devices.items()):
            lines.append(f"  device MISSING: {role}  [{count} topics]")
        for role, count in sorted(self.extra_devices.items()):
            lines.append(f"  device EXTRA:   {role}  [{count} topics]")
        for role, keys in sorted(self.missing_properties.items()):
            for key in keys:
                lines.append(f"  missing: {role}  {key}")
        for role, keys in sorted(self.extra_properties.items()):
            for key in keys:
                lines.append(f"  extra:   {role}  {key}")
        return "\n".join(lines) or "  (no structural difference)"


@dataclass(frozen=True)
class ValueReport:
    """Identity payloads that disagree between the two producers."""

    differing: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    """role -> ``info/...`` key -> ``[reference value, panelbench value]``."""

    def as_baseline(self) -> dict[str, dict[str, list[str]]]:
        return self.differing

    def describe(self) -> str:
        lines = [
            f"  {role}  {key}\n      reference:  {ref!r}\n      panelbench: {sub!r}"
            for role, keys in sorted(self.differing.items())
            for key, (ref, sub) in sorted(keys.items())
        ]
        return "\n".join(lines) or "  (every shared identity value agrees)"


def compare_identity_values(
    reference: dict[str, dict[str, str]], subject: dict[str, dict[str, str]]
) -> ValueReport:
    """Diff ``info/*`` payloads for devices and keys both producers publish.

    Keys only one side publishes are ``compare``'s finding, not this one.
    Reporting them here too would couple the two instruments, so that a single
    missing property has to be recorded — and later cleared — in two places.
    """
    ref = by_role(reference)
    sub = by_role(subject)

    differing: dict[str, dict[str, list[str]]] = {}
    for role in sorted(set(ref) & set(sub)):
        mismatched = {
            key: [ref[role][key], sub[role][key]]
            for key in sorted(set(ref[role]) & set(sub[role]))
            if key.startswith(f"{IDENTITY_NODE}/") and ref[role][key] != sub[role][key]
        }
        if mismatched:
            differing[role] = mismatched
    return ValueReport(differing=differing)


def compare(
    reference: dict[str, dict[str, str]], subject: dict[str, dict[str, str]]
) -> ParityReport:
    """Structural diff of *subject* against *reference*, aligned by role."""
    ref = by_role(reference)
    sub = by_role(subject)

    shared = set(ref) & set(sub)
    return ParityReport(
        missing_devices={r: len(ref[r]) for r in sorted(set(ref) - set(sub))},
        extra_devices={r: len(sub[r]) for r in sorted(set(sub) - set(ref))},
        missing_properties={
            r: sorted(set(ref[r]) - set(sub[r]))
            for r in sorted(shared)
            if set(ref[r]) - set(sub[r])
        },
        extra_properties={
            r: sorted(set(sub[r]) - set(ref[r]))
            for r in sorted(shared)
            if set(sub[r]) - set(ref[r])
        },
    )


def settable_properties(body: dict[str, str]) -> set[str]:
    """``node/property`` keys a device declares ``$settable``.

    The commissioning locks reach the wire only as the absence of this attribute,
    so ``compare``, which diffs which properties exist, cannot see a lost lock.
    """
    description = body.get("$description")
    if not description:
        return set()
    try:
        nodes = json.loads(description).get("nodes") or {}
    except json.JSONDecodeError:
        return set()
    return {
        f"{node_id}/{prop}"
        for node_id, node in nodes.items()
        for prop, declaration in (node.get("properties") or {}).items()
        if isinstance(declaration, dict) and declaration.get("settable") is True
    }


def compare_settable(
    reference: dict[str, dict[str, str]], subject: dict[str, dict[str, str]]
) -> dict[str, dict[str, list[str]]]:
    """Per shared role, the ``$settable`` declarations only one producer makes.

    Devices only one side publishes are ``compare``'s finding, not this one.
    """
    ref = by_role(reference)
    sub = by_role(subject)
    differing: dict[str, dict[str, list[str]]] = {}
    for role in sorted(set(ref) & set(sub)):
        ref_settable = settable_properties(ref[role])
        sub_settable = settable_properties(sub[role])
        if ref_settable != sub_settable:
            differing[role] = {
                "reference_only": sorted(ref_settable - sub_settable),
                "panelbench_only": sorted(sub_settable - ref_settable),
            }
    return differing
