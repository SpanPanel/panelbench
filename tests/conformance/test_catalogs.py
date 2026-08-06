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


# Concrete catalog units that `ebus_sdk.Unit` cannot express.
#
# Homie's unit is explicitly free-form ("you are not limited to the recommended values"),
# but the SDK models it as a closed enum, so a legitimate unit outside that enum cannot be
# published through it. Our wire layer resolves an unmodelled unit to None and omits it
# (ebus_emitter/wire/graph_builder.py), so composing one of these properties would publish
# it with no $unit at all — silently.
#
# Latent today: none of these properties is composed into a profile. The set is asserted
# exactly rather than merely allowed, so a re-vendor that adds one, or an SDK release that
# closes the gap, both show up as a failing test needing a deliberate edit.
SDK_UNMODELLED_UNITS: frozenset[str] = frozenset({"kA"})


def test_abstract_tokens_and_sdk_unit_gaps_are_both_known() -> None:
    """Guard against a re-vendor introducing a unit we would silently drop.

    Two distinct risks share one shape. ABSTRACT_UNITS is spec knowledge that does not
    travel with the vendored JSON and is ours to maintain. SDK_UNMODELLED_UNITS is an
    upstream SDK limitation. Both end with a property published without its unit, so both
    are checked here. ``ebus_sdk`` is imported in the test rather than in the package,
    which stays SDK-free.
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
    """The live version of the check above: what we actually publish must carry its unit.

    A unit the SDK cannot express is only a real defect once a profile composes the
    property. This fails the moment one does.
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
                    if catalog_prop.unit in ABSTRACT_UNITS | SDK_UNMODELLED_UNITS:
                        composed.append(
                            f"{path.name}:{device_type}/{node_id}/{prop_id} "
                            f"inherits unpublishable unit {catalog_prop.unit!r}"
                        )
    assert not composed, (
        "profile properties that would publish with no unit; give each an explicit "
        f"concrete unit in the profile selection: {composed}"
    )
