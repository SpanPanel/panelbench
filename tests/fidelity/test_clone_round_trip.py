"""A clone publishes the tree its source publishes: the public fidelity test for MAIN 32.

Each source panel's retained tree is cloned the way a live scrape clones it, the
clone is published, and the two trees are compared: which devices exist, what
each declares, and every value, literal form included, since a consumer reads
the literal. A difference fails unless it is one of these, each named below with
its reason:

- **Time-varying**: measurements and live state, which differ between any two
  moments of the same panel.
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
from panelbench.emitter_adapter.wire_capture import (
    as_capture,
    capture_retained,
    discovered_devices,
)
from tests._helpers import CAPTURED_MAIN_32, CAPTURED_MAIN_32_SERIAL

_REHEARSAL_AFTER = Path(__file__).resolve().parents[2] / "configs" / "rehearsal-after.yaml"

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
    serial = yaml.safe_load(_REHEARSAL_AFTER.read_text(encoding="utf-8"))["panel_config"][
        "serial_number"
    ]
    return await _round_trip(await capture_retained(_REHEARSAL_AFTER), serial, workdir)


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

_TIME_VARYING_NODES = frozenset({"meter", "power-flows", "soc", "shed-forecast"})
"""Measurements, which differ between any two moments of one panel."""

_TIME_VARYING_VALUES = frozenset(
    {
        "value $state",
        "description version",
        # Live state of the panel's envelope and of the grid, as of the capture.
        "value door/state",
        "value status/cloud-connection",
        "value status/wifi",
        "value status/wifi-ssid",
        "value grid/grid-state",
        "value switch/relay",
    }
)


def _time_varying(difference: Difference) -> bool:
    where = difference.where
    if where in _TIME_VARYING_VALUES:
        return True
    return where.startswith("value ") and where.removeprefix("value ").split("/")[0] in (
        _TIME_VARYING_NODES
    )


def _by_design(difference: Difference) -> bool:
    """What makes the clone a clone: its own serial; and the postal code, not copied."""
    if (
        difference.role == "distribution-enclosure"
        and difference.where == "value info/serial-number"
    ):
        return True
    return difference.where == "value status/postal-code"


def _numerically_equal(difference: Difference) -> bool:
    try:
        return float(str(difference.source)) == float(str(difference.clone))
    except ValueError:
        return False


UPSTREAM: dict[str, tuple[str, Callable[[Difference], bool]]] = {
    "an unpublished breaker rating is published": (
        "ebus-panel-sim 0.9.0 requires breaker-rating-a (manifest_physics._req_float), so a "
        "clone that records none must give it a placeholder, which it publishes",
        lambda d: d.where == "value breaker/rating" and d.source is None,
    ),
    "an unpublished pcs priority is published": (
        "ebus-panel-sim 0.9.0 publishes pcs/priority 0 for a circuit whose manifest names none",
        lambda d: d.where == "value pcs/priority" and d.source is None,
    ),
    "a number loses its literal form": (
        "ebus-panel-sim 0.9.0 parses numeric metadata to float and publishes str(float): "
        "81 becomes 81.0",
        lambda d: d.where.startswith("value ") and d.source is not None and _numerically_equal(d),
    ),
    "the shed policy is fixed": (
        "ebus-panel-sim 0.9.0 publishes a fixed soc-priority policy (20/30) unless one is set "
        "over MQTT; the manifest's threshold does not reach it",
        lambda d: d.where == "value shed/policy",
    ),
    "the PCS settings are fixed": (
        "ebus-panel-sim 0.9.0 publishes its own PCS settings and the off-grid import limit "
        "properties, whatever the panel's",
        lambda d: (
            d.role == "distribution-enclosure"
            and d.where.removeprefix("value ").removeprefix("declaration ").startswith("pcs/")
        ),
    ),
    "a commissioned circuit's relay requester": (
        "ebus-panel-sim 0.9.0 reports CONFIGURATION for a locked relay; SPAN reports PCS for "
        "the commissioned PV circuit",
        lambda d: d.where == "value switch/relay-requester",
    ),
    "connection/count is not published": (
        "ebus-panel-sim 0.9.0's profiles do not declare connection/count",
        lambda d: d.where == "declaration connection/count",
    ),
    "a circuit is named for people": (
        "ebus-panel-sim 0.9.0 publishes one display name as both a circuit's $description "
        "name and its info/name; SPAN release 202639 names the device after its id and keeps "
        "the circuit's own name in info/name, which naming the device by id here would lose",
        lambda d: d.where == "description name" and d.role.startswith("circuit"),
    ),
    "a property's declared name differs": (
        "ebus-panel-sim 0.9.0's profiles word some declarations differently from SPAN firmware",
        lambda d: (
            d.where.startswith("declaration ")
            and isinstance(d.source, dict)
            and isinstance(d.clone, dict)
            and {k: v for k, v in d.source.items() if k != "name"}
            == {k: v for k, v in d.clone.items() if k != "name"}
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
