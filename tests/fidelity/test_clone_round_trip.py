"""A clone publishes the tree its source publishes: the public fidelity test for MAIN 32.

Each source panel's retained tree is cloned the way a live scrape clones it, the
clone is published, and the two trees are compared: which devices exist, what
each declares, and every value, literal form included, since a consumer reads
the literal. A difference fails unless it is one of these, each named below with
its reason:

- **Time-varying**: measurements and timestamps, which differ between any two
  moments of the same panel, and the panel's door and cloud state, live state the
  emulator simulates as it does power. Never configuration: the panel's network
  links and SSID, which a clone copies, are compared like any other value.
- **By design**: what makes the clone a clone, its own serial and device ids, and
  the postal code, which a clone deliberately does not copy. Device ids are
  compared through the clone's mapping of them, so a reference to the wrong device
  still fails.
- **Upstream**: what the pinned emitter, ebus-panel-sim, publishes whatever
  PanelBench gives it. Each is a strict expected failure naming the cause, so one
  the emitter fixes turns into a failure here, and the expectation comes out.

Two sources: the pinned release's masked capture of a real MAIN 32 on SPAN release
202639, and the rehearsal-after config as PanelBench publishes it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from panelbench.clone import translate_panel_tree, write_clone_config
from panelbench.config_types import SimulationConfig
from panelbench.emitter_adapter.wire_capture import (
    as_capture,
    capture_retained,
    discovered_devices,
)
from panelbench.hardware import status_hardware_version
from tests._helpers import (
    CAPTURED_MAIN_32,
    CAPTURED_MAIN_32_SERIAL,
    REHEARSAL_ADDED_INVERTER,
    rehearsal_after,
    rehearsal_before,
    write_config,
)

Capture = dict[str, dict[str, str]]


@dataclass(frozen=True)
class Difference:
    """One thing the clone publishes differently from its source."""

    role: str
    where: str
    source: object
    clone: object

    def __str__(self) -> str:
        return f"{self.role}: {self.where}: source {self.source!r}, clone {self.clone!r}"


# -- the two trees ---------------------------------------------------------------


def _retained_from_snapshot(path: Path) -> dict[str, bytes]:
    """A ``tree-v1`` snapshot as the retained topics its panel publishes."""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    retained: dict[str, bytes] = {}
    for device_id, entry in snapshot["devices"].items():
        retained[f"ebus/5/{device_id}/$description"] = json.dumps(entry["description"]).encode()
        for section in ("properties", "numeric_properties"):
            for key, value in (entry.get(section) or {}).items():
                if value is not None:
                    retained[f"ebus/5/{device_id}/{key}"] = str(value).encode()
    return retained


async def _round_trip(
    source: Mapping[str, bytes], serial: str, workdir: Path
) -> tuple[Capture, Capture]:
    """*source*, and its clone as published, each as a consumer replays it."""
    config = translate_panel_tree(serial, discovered_devices(source))
    clone = await capture_retained(write_clone_config(config, workdir, serial))
    return as_capture(dict(source)), as_capture(clone)


async def _captured_main_32(workdir: Path) -> tuple[Capture, Capture]:
    return await _round_trip(
        _retained_from_snapshot(CAPTURED_MAIN_32), CAPTURED_MAIN_32_SERIAL, workdir
    )


async def _rehearsal_after(workdir: Path) -> tuple[Capture, Capture]:
    """The README's rehearsal-after panel, built by the recipe its own test follows."""
    config = rehearsal_after(rehearsal_before(REHEARSAL_ADDED_INVERTER), REHEARSAL_ADDED_INVERTER)
    source = write_config(workdir / "rehearsal-after.yaml", config)
    serial = config["panel_config"]["serial_number"]
    return await _round_trip(await capture_retained(source), serial, workdir)


# -- aligning and comparing ------------------------------------------------------


def _description(body: Mapping[str, str]) -> dict[str, object]:
    raw = body.get("$description")
    parsed = json.loads(raw) if raw else {}
    assert isinstance(parsed, dict)
    return parsed


def _class(body: Mapping[str, str]) -> str:
    return str(_description(body).get("type", "?")).rsplit(".", 1)[-1]


def _role(device_id: str, tree: Capture) -> str:
    """Where *device_id* sits, the same in a panel and its clone, whatever its id.

    A circuit by the spaces it occupies; an inverter, battery or SPAN Drive by the
    circuit or lugs it hangs from; a MID by its battery; lugs by direction.
    """
    body = tree[device_id]
    kind = _class(body)
    if kind == "circuit":
        return f"circuit @{body.get('info/spaces')}"
    if kind in ("pv", "bess", "evse"):
        for other in tree.values():
            if _class(other) == "circuit" and other.get("connection/feeds-device-id") == device_id:
                return f"{kind} @{other.get('info/spaces')}"
            if _class(other) == "lugs" and other.get("connection/fed-by-device-id") == device_id:
                return f"{kind} @{other.get('info/direction')} lugs"
        return f"{kind} @nowhere"
    if kind == "mid":
        return f"mid of {_role(str(_description(body).get('parent')), tree)}"
    if kind == "lugs":
        return f"lugs {body.get('info/direction')}"
    return kind


def _by_role(tree: Capture) -> dict[str, str]:
    roles: dict[str, str] = {}
    for device_id in tree:
        role = _role(device_id, tree)
        assert role not in roles, f"two devices sit at {role}; comparing would merge them"
        roles[role] = device_id
    return roles


def _differences(source: Capture, clone: Capture) -> list[Difference]:
    """Everything the clone publishes differently, device ids read through the mapping."""
    source_roles, clone_roles = _by_role(source), _by_role(clone)
    to_clone_id = {
        source_roles[role]: clone_roles[role] for role in source_roles.keys() & clone_roles.keys()
    }

    def mapped(value: object) -> object:
        if isinstance(value, str):
            return to_clone_id.get(value, value)
        if isinstance(value, list):
            return sorted(str(mapped(item)) for item in value)
        return value

    found = [
        Difference(role, "device", "present", None)
        for role in source_roles.keys() - clone_roles.keys()
    ]
    found += [
        Difference(role, "device", None, "present")
        for role in clone_roles.keys() - source_roles.keys()
    ]
    for role in sorted(source_roles.keys() & clone_roles.keys()):
        theirs, ours = source[source_roles[role]], clone[clone_roles[role]]
        found += _description_differences(role, _description(theirs), _description(ours), mapped)
        for key in sorted((theirs.keys() | ours.keys()) - {"$description"}):
            if mapped(theirs.get(key)) != ours.get(key):
                found.append(Difference(role, f"value {key}", theirs.get(key), ours.get(key)))
    return found


def _description_differences(
    role: str,
    theirs: dict[str, object],
    ours: dict[str, object],
    mapped: Callable[[object], object],
) -> list[Difference]:
    found: list[Difference] = []
    for field in sorted((theirs.keys() | ours.keys()) - {"nodes"}):
        if mapped(theirs.get(field)) != mapped(ours.get(field)):
            found.append(
                Difference(role, f"description {field}", theirs.get(field), ours.get(field))
            )
    their_nodes = theirs.get("nodes") or {}
    our_nodes = ours.get("nodes") or {}
    assert isinstance(their_nodes, dict) and isinstance(our_nodes, dict)
    for node in sorted(their_nodes.keys() | our_nodes.keys()):
        if node not in our_nodes or node not in their_nodes:
            found.append(Difference(role, f"node {node}", node in their_nodes, node in our_nodes))
            continue
        their_props = their_nodes[node].get("properties") or {}
        our_props = our_nodes[node].get("properties") or {}
        for prop in sorted(their_props.keys() | our_props.keys()):
            if their_props.get(prop) != our_props.get(prop):
                found.append(
                    Difference(
                        role,
                        f"declaration {node}/{prop}",
                        their_props.get(prop),
                        our_props.get(prop),
                    )
                )
    return found


# -- what a difference may be ----------------------------------------------------
#
# Each allowance is pinned to the property, the device and the values it was observed
# with, so it cannot also hide a regression elsewhere that happens to look alike. A
# strict expectation is only as narrow as its predicate.

_MEASUREMENTS = frozenset({"power-flows", "soc", "shed-forecast"})
"""Nodes whose every value is a measurement, different between any two moments."""

_LIVE_STATE = frozenset({"value door/state", "value status/cloud-connection"})
"""The panel's own live state, which the emulator simulates rather than the clone
copying: a copy would freeze the door opened to register the clone, or a cloud outage
at that moment, for good."""

_METER_MEASUREMENTS = frozenset(
    {"active-power", "current", "current-a", "current-b", "imported-energy", "exported-energy"}
)
"""The meter's measurements. Not its voltages: those are the panel's, and a clone keeps them."""


def _time_varying(difference: Difference) -> bool:
    """A measurement, a timestamp or the panel's live state. Never configuration."""
    if difference.where in ("value $state", "description version"):
        return True
    if difference.role == _PANEL and difference.where in _LIVE_STATE:
        return True
    if not difference.where.startswith("value "):
        return False
    node, _, prop = difference.where.removeprefix("value ").partition("/")
    return node in _MEASUREMENTS or (node == "meter" and prop in _METER_MEASUREMENTS)


def _by_design(difference: Difference) -> bool:
    """What makes the clone a clone: its own serial; and the postal code, not copied."""
    if (
        difference.role == "distribution-enclosure"
        and difference.where == "value info/serial-number"
    ):
        return True
    return (
        difference.role == "distribution-enclosure"
        and difference.where == "value status/postal-code"
    )


def _is(difference: Difference, role: str, where: str, source: object, clone: object) -> bool:
    return (difference.role, difference.where, difference.source, difference.clone) == (
        role,
        where,
        source,
        clone,
    )


def _on_a_circuit(difference: Difference) -> bool:
    return difference.role.startswith("circuit @")


_PANEL = "distribution-enclosure"
_COMMISSIONED_PV_CIRCUIT = "circuit @29,31"
"""The captured MAIN 32's "Commissioned PV System" circuit."""

_FIXED_SHED_POLICY = (
    '{"algorithm": "soc-priority.v1", '
    '"parameters": {"soc-threshold-shed": 20, "soc-threshold-release": 30}}'
)

_FIXED_PCS = {
    "binding-constraint": ("FSR", "NONE"),
    "enabled": ("true", "false"),
    "feed-import-limit": ("160.0", "0.0"),
    "feed-import-limit-active": ("true", "false"),
    "feed-import-limit-enablement": ("ENABLED", "UNCONFIGURED"),
    "import-limit": ("160.0", "0.0"),
}
"""The six PCS values the captured panel publishes, and the emitter's own for each."""


def _pcs_fixed(d: Difference) -> bool:
    prop = d.where.removeprefix("value pcs/")
    return (
        d.role == _PANEL
        and d.where.startswith("value pcs/")
        and _FIXED_PCS.get(prop) == (d.source, d.clone)
    )


UPSTREAM: dict[str, tuple[str, Callable[[Difference], bool]]] = {
    "one voltage for both legs": (
        "ebus-panel-sim 0.10.0b1 publishes one per-leg voltage on both legs, so a clone "
        "carries the legs' mean, published to the panel's one decimal",
        lambda d: (
            _is(d, _PANEL, "value meter/voltage-a", "121.8", "122.0")
            or _is(d, _PANEL, "value meter/voltage-b", "122.1", "122.0")
        ),
    ),
    "the shed policy is fixed": (
        "ebus-panel-sim 0.10.0b1 publishes a fixed soc-priority policy unless one is set over "
        "MQTT; the manifest's shed threshold does not reach it, and LoadSheddingConfig has no "
        "field for the release threshold (51 here)",
        lambda d: (
            d.role == _PANEL and d.where == "value shed/policy" and d.clone == _FIXED_SHED_POLICY
        ),
    ),
    "the PCS settings are fixed": (
        "ebus-panel-sim 0.10.0b1 publishes its own value for these six PCS properties, whatever "
        "the panel's",
        _pcs_fixed,
    ),
    "a commissioned circuit's relay requester": (
        "ebus-panel-sim 0.10.0b1 reports CONFIGURATION for a locked relay; SPAN reports PCS for "
        "the commissioned PV circuit (filed upstream as #66)",
        lambda d: _is(
            d, _COMMISSIONED_PV_CIRCUIT, "value switch/relay-requester", "PCS", "CONFIGURATION"
        ),
    ),
    "the MID's grid state is derived": (
        "ebus-panel-sim 0.10.0b1 derives the MID's grid-state from the tick (UP); the captured "
        "panel's MID publishes UNKNOWN",
        lambda d: (
            d.role.startswith("mid of ")
            and (d.where, d.source, d.clone) == ("value grid/grid-state", "UNKNOWN", "UP")
        ),
    ),
    "connection/count is not published": (
        "ebus-panel-sim 0.10.0b1's profiles do not declare connection/count",
        lambda d: (
            (_on_a_circuit(d) or d.role.startswith("lugs "))
            and d.where == "declaration connection/count"
            and d.clone is None
        ),
    ),
}


def _unexplained(differences: list[Difference]) -> list[Difference]:
    known = [check for _reason, check in UPSTREAM.values()]
    return [
        d
        for d in differences
        if not (_time_varying(d) or _by_design(d) or any(check(d) for check in known))
    ]


def _report(differences: list[Difference]) -> str:
    return "\n".join(str(d) for d in differences)


_SOURCES = {
    "captured MAIN 32 r202639": _captured_main_32,
    "rehearsal-after": _rehearsal_after,
}


# -- the tests -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("source", sorted(_SOURCES))
async def test_a_clone_differs_from_its_source_only_as_explained(
    source: str, tmp_path: Path
) -> None:
    theirs, ours = await _SOURCES[source](tmp_path)

    unexplained = _unexplained(_differences(theirs, ours))

    assert not unexplained, f"the clone of {source} differs:\n{_report(unexplained)}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group",
    [
        pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=f"upstream: {reason}"))
        for name, (reason, _check) in sorted(UPSTREAM.items())
    ],
)
async def test_the_captured_main_32_clone_has_no_known_difference(
    group: str, tmp_path: Path
) -> None:
    """Each known difference, held as an expectation that fails while it stands."""
    _reason, check = UPSTREAM[group]
    theirs, ours = await _captured_main_32(tmp_path)

    standing = [d for d in _differences(theirs, ours) if check(d)]

    assert not standing, f"{group}:\n{_report(standing)}"


@pytest.mark.asyncio
async def test_a_clone_carries_on_from_its_panels_energy(tmp_path: Path) -> None:
    """Energy is time-varying, but a clone's registers start where the panel's stood,
    not at zero: the first tick adds a few watt-hours at most."""
    theirs, ours = await _captured_main_32(tmp_path)
    source_roles, clone_roles = _by_role(theirs), _by_role(ours)

    for role, source_id in source_roles.items():
        if not role.startswith("circuit"):
            continue
        for register in ("meter/exported-energy", "meter/imported-energy"):
            published = theirs[source_id].get(register)
            if published is None:
                continue
            cloned = float(ours[clone_roles[role]][register])
            assert float(published) <= cloned < float(published) + 50.0, (role, register)


def test_a_clone_of_the_captured_main_32_reports_its_hardware_version_over_rest(
    tmp_path: Path,
) -> None:
    """MQTT already round-trips unmasked above; the REST status reads the same value, so
    the clone's ``hardwareVersion`` is the one the captured panel publishes."""
    source = _retained_from_snapshot(CAPTURED_MAIN_32)
    published = source[f"ebus/5/{CAPTURED_MAIN_32_SERIAL}/info/hardware-version"].decode()
    translated = translate_panel_tree(CAPTURED_MAIN_32_SERIAL, discovered_devices(source))
    written = write_clone_config(translated, tmp_path, CAPTURED_MAIN_32_SERIAL)

    clone: SimulationConfig = yaml.safe_load(written.read_text(encoding="utf-8"))

    assert status_hardware_version(clone) == published
