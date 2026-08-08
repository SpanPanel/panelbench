"""Every published `enum` payload is a member of its catalog `format`.

This is the instrument that would have caught §2.2.1 mechanically. panelbench's
vendored copy published `"ON_GRID"`/`"OFF_GRID"` on `grid/grid-state`, whose
catalog format is `UP,DOWN,DEGRADED,UNKNOWN` — flat-schema vocabulary sitting in
a v1.0 slot. The value it should have carried belongs to the property next door:
`grid/islanding-state` is exactly `ON_GRID,OFF_GRID,UNKNOWN`. Nothing failed.

**Why the conformance checker could not do this.** Its model is declarations, by
design — `feeds.py` says so ("Ways of obtaining $description documents"), and
`HomieProperty` carries `datatype`, `unit`, `format`, `settable`, `retained`, and
no value. Giving `HomieTree` values to reach this rule would make
`ConformanceReport` tick-dependent, so a conformance profile would stop being a
stable artifact and `golden_tree.json` would change meaning. The report answers
"is this tree's declared contract legal"; this answers "is this snapshot's state
legal". Two questions, and only the second one needs the wire.

**Why this may assert values where its neighbour may not.** `test_wire_capture.py`
holds the line that structure is asserted and values never are, because
`noise_factor` and a running clock move power and current every run. An enum is
the one payload immune to that: membership in a closed set is stable under noise,
so this is not a value assertion so much as a vocabulary one.

It runs against a **fresh** capture rather than `golden_wire.json`, because what
it is really guarding is the emitter — including the pinned fork. The committed
fixture's staleness check compares device and topic *sets*, so a value that
turned illegal without changing shape would not disturb it.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import pytest
import pytest_asyncio

from panelbench.conformance import (
    Catalog,
    build_tree,
    emitter_catalogs,
    load_catalogs,
)
from panelbench.emitter_adapter.wire_capture import capture

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
_CONFIG = _REPO / "configs" / "default_MAIN_40.yaml"

MULTI_VALUED = frozenset({"energy.ebus.capability.breaker::protection-functions"})
"""Enum properties whose payload is a *set* of members rather than one member.

The catalog marks this in prose, not in a field: `protection-functions` is
described as a "Multi-valued set of the protections this breaker provides", while
every other enum names one state. So the exception is listed here explicitly
rather than inferred, and a second multi-valued property arriving on a pin bump
fails `test_the_multi_valued_exception_is_still_the_only_one` — which names the
cause — instead of surfacing as a mystery membership failure here.
"""


@dataclass(frozen=True)
class EnumValue:
    """One published payload on a property the catalog declares as an enum."""

    device: str
    capability: str
    node: str
    property: str
    value: str
    allowed: frozenset[str]

    @property
    def key(self) -> str:
        return f"{self.capability}::{self.property}"

    def members(self) -> list[str]:
        """The payload's members: one, or several for a multi-valued set."""
        return self.value.split(",") if self.key in MULTI_VALUED else [self.value]

    def illegal(self) -> list[str]:
        return [m for m in self.members() if m not in self.allowed]

    def describe(self) -> str:
        return (
            f"{self.device}/{self.node}/{self.property} published {self.value!r}; "
            f"catalog {self.capability} allows {sorted(self.allowed)}"
        )


def _enum_values(
    captured: dict[str, dict[str, str]], catalogs: dict[str, Catalog]
) -> list[EnumValue]:
    """Every captured payload whose *catalog* datatype is `enum`.

    Keyed off the catalog rather than the published `$description`, deliberately.
    A publisher that declared its own narrower format and published within it
    would be self-consistent and still wrong the way §2.2.1 was wrong — the
    question is whether the value means what the specification says it means.
    """
    found: list[EnumValue] = []
    for device_id, body in captured.items():
        description = body.get("$description")
        if description is None:
            continue
        device = build_tree({device_id: json.loads(description)}).devices[device_id]
        for node_id, node in device.nodes.items():
            catalog = catalogs.get(node.type) if node.type is not None else None
            if catalog is None:
                continue
            for property_id in node.properties:
                catalog_property = catalog.properties.get(property_id)
                if catalog_property is None or catalog_property.datatype != "enum":
                    continue
                value = body.get(f"{node_id}/{property_id}")
                if value is None:
                    continue
                found.append(
                    EnumValue(
                        device=device_id,
                        capability=catalog.capability,
                        node=node_id,
                        property=property_id,
                        value=value,
                        allowed=frozenset((catalog_property.format or "").split(",")),
                    )
                )
    return found


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def published() -> list[EnumValue]:
    return _enum_values(await capture(_CONFIG), load_catalogs(emitter_catalogs()))


@pytest.mark.asyncio(loop_scope="module")
async def test_every_published_enum_value_is_a_catalog_member(
    published: list[EnumValue],
) -> None:
    """The instrument.

    Failure here is one of two things: a value that means nothing to a consumer
    keying on the catalog, or a property that has quietly become multi-valued
    upstream — see `MULTI_VALUED`.
    """
    offenders = [value.describe() for value in published if value.illegal()]

    assert not offenders, "enum payloads outside their catalog format:\n" + "\n".join(offenders)


@pytest.mark.asyncio(loop_scope="module")
async def test_the_check_examined_a_meaningful_number_of_values(
    published: list[EnumValue],
) -> None:
    """Guards the premise, because the assertion above passes vacuously on nothing.

    Every way this check could stop measuring is silent: a renamed node type stops
    resolving to a catalog, a capability drops out of the profile, a capture
    arrives with declarations and no values. The floor is well under the 105
    instances and 16 distinct properties measured when this was written, so
    ordinary config edits do not trip it, but a collapse does.
    """
    assert len(published) > 60, (
        f"only {len(published)} enum payloads found; this check has stopped measuring "
        "most of the tree rather than found it clean"
    )
    distinct = {value.key for value in published}
    assert len(distinct) > 10, f"only {len(distinct)} distinct enum properties: {sorted(distinct)}"


def test_the_multi_valued_exception_is_still_the_only_one() -> None:
    """`MULTI_VALUED` relaxes the check, so it has to stay justified by the catalog.

    Two ways it rots, both silent without this. A property listed here stops being
    multi-valued, and the exception becomes a blanket licence to publish
    comma-joined junk on a single-valued enum. Or the emitter pin brings a *new*
    multi-valued enum, and the check above fails on a legal payload with a message
    pointing at the wrong thing.
    """
    catalogs = load_catalogs(emitter_catalogs())
    documented = set()
    for path in sorted(pathlib.Path(emitter_catalogs()).glob("*.json")):
        raw = json.loads(path.read_text())
        for property_id, spec in raw.get("properties", {}).items():
            if spec.get("datatype") != "enum":
                continue
            if "multi-valued" in str(spec.get("description", "")).lower():
                documented.add(f"{raw['capability']}::{property_id}")

    assert documented == set(MULTI_VALUED), (
        f"catalogs describe {sorted(documented)} as multi-valued enums, but MULTI_VALUED "
        f"holds {sorted(MULTI_VALUED)}. Reconcile before trusting the membership check."
    )
    for key in MULTI_VALUED:
        capability, property_id = key.split("::")
        assert property_id in catalogs[capability].properties, (
            f"{key} is excepted but no longer exists in the catalog"
        )
