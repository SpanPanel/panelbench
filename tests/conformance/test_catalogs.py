from __future__ import annotations

from pathlib import Path

import pytest

from panelbench.conformance.catalogs import (
    ABSTRACT_UNITS,
    CatalogError,
    load_catalogs,
)
from panelbench.conformance.emitter_data import emitter_catalogs, emitter_profiles

# What the producer published from. The byte-identical spec copies under
# spec/catalogs are provenance's business, and the two are tied together by
# test_the_wheel_catalogs_are_the_ones_provenance_vouches_for below.
VENDORED = emitter_catalogs()


def test_loads_vendored_catalogs_keyed_by_capability_type() -> None:
    catalogs = load_catalogs(VENDORED)
    assert "energy.ebus.capability.soc" in catalogs
    soc = catalogs["energy.ebus.capability.soc"]
    # Version pinned so a re-vendor is a deliberate edit here rather than a silent
    # one; `.ebus-spec.json` carries the same number and provenance holds the two
    # together. 0.1 -> 0.2 arrived with the spec 4085c68 re-vendor.
    assert soc.version == "0.2"
    assert soc.properties["soc"].unit == "%"
    # The abstract token, not a concrete unit: the spec requires a publisher to
    # substitute one, which is what
    # `test_no_composed_profile_property_uses_an_unpublishable_unit` below checks the
    # profiles actually do.
    assert soc.properties["soe"].unit == "energy"


def test_rejects_a_directory_with_no_catalogs(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="no capability catalogs"):
        load_catalogs(tmp_path)


# Catalog units that `ebus_sdk.Unit` does not model.
#
# `graph_builder._to_sdk_unit` does NOT pass an unmodelled unit through as a string, as this
# note used to say. It resolves by value, catches the `ValueError` and returns None, so the
# unit is omitted from the wire entirely. Homie's unit is free-form and would have carried
# the string happily; the SDK enum is the narrower party.
#
# Not a live publishing risk, but only because of what is in the set rather than because a
# drop would be harmless. The single entry, `kA`, is `breaker/interrupting-rating`, which no
# profile composes, so nothing reaches the wire missing a unit today. That makes the exact
# assertion below the thing keeping this true, not a formality.
#
# Tracked anyway because the set is a useful signal in both directions: a new entry after a
# re-vendor is worth a look (typo, or a genuinely new unit), and a shrinking one means an
# SDK release added a member. Asserted exactly rather than merely allowed so either change
# needs a deliberate edit rather than passing unnoticed.
SDK_UNMODELLED_UNITS: frozenset[str] = frozenset({"kA"})


def test_abstract_tokens_and_sdk_unit_coverage_are_both_known() -> None:
    """Pin what we know about catalog units, in both categories.

    ABSTRACT_UNITS is spec knowledge that does not travel with the vendored JSON and is
    ours to maintain; an unhandled token would be a real defect. SDK_UNMODELLED_UNITS is
    informational only, per the note above. ``ebus_sdk`` is imported in the test rather
    than in the package, which stays SDK-free.
    """
    import ebus_sdk

    unmodelled: set[str] = set()
    for catalog in load_catalogs(VENDORED).values():
        for prop in catalog.properties.values():
            if prop.unit is None or prop.unit in ABSTRACT_UNITS:
                continue
            try:
                ebus_sdk.Unit(prop.unit)
            except ValueError:
                unmodelled.add(prop.unit)

    assert unmodelled == SDK_UNMODELLED_UNITS, (
        f"catalog units the SDK cannot express changed: {sorted(unmodelled)} vs known "
        f"{sorted(SDK_UNMODELLED_UNITS)}. A new entry means a re-vendor added a unit the "
        "SDK enum lacks (report upstream, and do not compose that property until it is "
        "fixed). A removed entry means an SDK release closed the gap."
    )


def test_no_composed_profile_property_uses_an_unpublishable_unit() -> None:
    """A composed property must not inherit an abstract unit token.

    Not the belt to profile_loader's braces this once claimed to be — it is the belt.
    The loader does not reject a token: `_hydrate_property` resolves a light selection
    with `sel.get("unit", catalog_def.get("unit"))` and inherits `energy` unexamined,
    and nothing in `ebus_panel_sim` knows abstract units exist. Downstream,
    `graph_builder._to_sdk_unit` cannot resolve `Unit("energy")` either, so it catches
    the `ValueError` and returns None and the property publishes with no `$unit` at
    all. Silent omission, not a loud failure, is what this test exists to prevent;
    `conformance/rules.py` catches the same thing again on a captured wire.

    Only ABSTRACT_UNITS is checked here, because only it is a spec violation — a
    publisher MUST substitute a concrete unit. A unit the SDK merely does not model is
    a different problem, tracked by SDK_UNMODELLED_UNITS above; it is dropped by that
    same `_to_sdk_unit` path, which is why the guidance there is to leave such a
    property uncomposed until the SDK gains the member.
    """
    import json

    composed: list[str] = []
    catalogs = load_catalogs(VENDORED)
    for path in sorted(emitter_profiles().rglob("*.json")):
        raw = json.loads(path.read_text())
        for device_type in raw.get("device_types", {}).values():
            for node_id, use in device_type.get("capabilities", {}).items():
                catalog = catalogs.get(use.get("catalog", ""))
                if catalog is None:
                    continue
                for prop_id, selection in use.get("properties", {}).items():
                    catalog_prop = catalog.properties.get(prop_id)
                    if catalog_prop is None or catalog_prop.unit is None:
                        continue
                    # An explicit unit in the selection is the substitution we want.
                    if selection.get("unit") is not None:
                        continue
                    if catalog_prop.unit in ABSTRACT_UNITS:
                        composed.append(
                            f"{path.name}:{device_type}/{node_id}/{prop_id} "
                            f"inherits unpublishable unit {catalog_prop.unit!r}"
                        )
    assert not composed, (
        "profile properties that would publish with no unit; give each an explicit "
        f"concrete unit in the profile selection: {composed}"
    )


def test_the_wheel_catalogs_are_the_ones_provenance_vouches_for() -> None:
    """The link that makes the whole chain of custody hold.

    ``check-spec-provenance.py`` proves ``spec/catalogs`` is byte-identical to the
    eBus specification at the commit ``.ebus-spec.json`` pins. Every conformance
    check above measures against the *wheel's* catalogs instead, because those are
    the bytes the emitter actually composed the published tree from.

    Those are two different files, and without this the chain has a hole in the
    middle: the specification would vouch for a copy nothing reads, and
    conformance would measure against data nothing vouched for. A pin bump that
    quietly changed a catalog would pass every other test in this file.

    Failing here means the emitter's catalogs and our pinned specification commit
    have diverged. Re-sync ``spec/catalogs`` and update ``synced_commit``, or hold
    the pin — but do it deliberately.
    """
    vendored = Path("spec/catalogs")
    wheel = emitter_catalogs()

    ours = {p.name: p.read_bytes() for p in vendored.glob("*.json")}
    theirs = {p.name: p.read_bytes() for p in wheel.glob("*.json")}

    assert ours, f"no vendored catalogs under {vendored}"
    assert set(ours) == set(theirs), (
        "the emitter ships a different set of catalogs than we vendored: "
        f"only in the wheel {sorted(set(theirs) - set(ours))}, "
        f"only vendored {sorted(set(ours) - set(theirs))}"
    )
    differing = sorted(name for name, body in ours.items() if theirs[name] != body)
    assert not differing, f"catalogs that differ between the wheel and spec/catalogs: {differing}"
