from __future__ import annotations

from pathlib import Path

import pytest

from span_panel_simulator.conformance.catalogs import (
    ABSTRACT_UNITS,
    CatalogError,
    load_catalogs,
)

VENDORED = Path("src/span_panel_simulator/ebus_emitter/wire/catalogs")


def test_loads_vendored_catalogs_keyed_by_capability_type() -> None:
    catalogs = load_catalogs(VENDORED)
    assert "energy.ebus.capability.soc" in catalogs
    soc = catalogs["energy.ebus.capability.soc"]
    assert soc.version == "0.1"
    assert soc.properties["soc"].unit == "%"
    assert soc.properties["soe"].unit == "energy"


def test_rejects_a_directory_with_no_catalogs(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="no capability catalogs"):
        load_catalogs(tmp_path)


# Catalog units that `ebus_sdk.Unit` does not model.
#
# NOT a defect and NOT a publishing risk: Homie's unit is free-form, the SDK accepts and
# publishes a plain string verbatim, and `graph_builder._to_sdk_unit` passes an unmodelled
# unit through as a string. `Unit` is a convenience vocabulary, not a constraint.
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

    Belt to profile_loader's braces: the loader raises on a token at hydration time, and
    this states the same requirement over the profile JSON directly, so the invariant is
    visible here rather than only as loader behaviour.

    Only ABSTRACT_UNITS makes a unit unpublishable. A unit the SDK enum does not model is
    published verbatim as a string, so SDK_UNMODELLED_UNITS is not checked here.
    """
    import json

    composed: list[str] = []
    catalogs = load_catalogs(VENDORED)
    for path in sorted(
        Path("src/span_panel_simulator/ebus_emitter/wire/profiles").rglob("*.json")
    ):
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
