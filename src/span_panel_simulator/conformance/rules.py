"""Conformance rules over a parsed Homie tree.

Two kinds of output, and the split is the whole design:

* **Violations** break Homie 5 or one of eBus's few explicit MUSTs. They fail a build.
* **Observations** record legal divergence, extension and omission. They never fail.

The specification is deliberately permissive - publishing is opt-in, datatypes may be
widened, uncatalogued properties are allowed - so a checker that gates on catalog match
would fail conformant publishers. See docs/spec-conformance-design.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .catalogs import ABSTRACT_UNITS, Catalog
from .device_profiles import DeviceProfile
from .model import HomieDevice, HomieNode, HomieProperty, HomieTree

# Homie 5 marks `format` required for exactly these datatypes and optional for all others.
_FORMAT_REQUIRED_DATATYPES = frozenset({"enum", "color"})

_EBUS_CAPABILITY_PREFIX = "energy.ebus.capability."
_NUMERIC_DATATYPES = frozenset({"integer", "float"})


class Severity(enum.Enum):
    VIOLATION = "violation"
    OBSERVATION = "observation"


class Bucket(enum.Enum):
    MATCH = "match"
    DIVERGENCE = "divergence"
    EXTENSION = "extension"
    OMISSION = "omission"
    VIOLATION = "violation"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    bucket: Bucket
    message: str
    device: str
    node: str | None = None
    property: str | None = None


def _violation(
    rule: str,
    message: str,
    device: str,
    node: str | None = None,
    prop: str | None = None,
) -> Finding:
    return Finding(
        rule=rule,
        severity=Severity.VIOLATION,
        bucket=Bucket.VIOLATION,
        message=message,
        device=device,
        node=node,
        property=prop,
    )


def _observation(
    rule: str,
    bucket: Bucket,
    message: str,
    device: str,
    node: str | None = None,
    prop: str | None = None,
) -> Finding:
    return Finding(
        rule=rule,
        severity=Severity.OBSERVATION,
        bucket=bucket,
        message=message,
        device=device,
        node=node,
        property=prop,
    )


def _check_property(
    device: HomieDevice, node: HomieNode, prop: HomieProperty, catalog: Catalog | None
) -> list[Finding]:
    findings: list[Finding] = []

    if prop.datatype in _FORMAT_REQUIRED_DATATYPES and prop.format is None:
        findings.append(
            _violation(
                "V1",
                f"datatype '{prop.datatype}' requires a format in Homie 5, none published",
                device.id,
                node.id,
                prop.id,
            )
        )

    if prop.unit is not None and prop.unit in ABSTRACT_UNITS:
        findings.append(
            _violation(
                "V2",
                f"unit '{prop.unit}' is an abstract token and is never valid on the wire; "
                "a publisher must substitute a concrete unit",
                device.id,
                node.id,
                prop.id,
            )
        )

    catalog_prop = catalog.properties.get(prop.id) if catalog is not None else None
    if (
        catalog_prop is not None
        and catalog_prop.unit is not None
        and catalog_prop.unit in ABSTRACT_UNITS
        and prop.unit is None
    ):
        findings.append(
            _violation(
                "V3",
                f"catalog unit '{catalog_prop.unit}' is an abstract token, so a concrete "
                "unit must be substituted, but no unit was published",
                device.id,
                node.id,
                prop.id,
            )
        )

    return findings


def check_violations(tree: HomieTree, catalogs: dict[str, Catalog]) -> list[Finding]:
    """Every rule that fails a build. Empty means conformant."""
    findings: list[Finding] = []

    for device in tree.devices.values():
        for child_id in device.children:
            if child_id not in tree.devices:
                findings.append(
                    _violation(
                        "V4",
                        f"child device '{child_id}' is named but publishes no description",
                        device.id,
                    )
                )
        for node in device.nodes.values():
            catalog = catalogs.get(node.type) if node.type is not None else None
            for prop in node.properties.values():
                findings.extend(_check_property(device, node, prop, catalog))

    return findings


def _observe_property(
    device: HomieDevice, node: HomieNode, prop: HomieProperty, catalog: Catalog
) -> list[Finding]:
    findings: list[Finding] = []
    catalog_prop = catalog.properties.get(prop.id)

    if catalog_prop is None:
        findings.append(
            _observation(
                "O6",
                Bucket.EXTENSION,
                f"property '{prop.id}' is not in catalog {catalog.capability}; published "
                "as an extension, which the specification permits",
                device.id,
                node.id,
                prop.id,
            )
        )
        return findings

    if prop.datatype != catalog_prop.datatype:
        findings.append(
            _observation(
                "O2",
                Bucket.DIVERGENCE,
                f"datatype '{prop.datatype}' differs from catalog '{catalog_prop.datatype}'",
                device.id,
                node.id,
                prop.id,
            )
        )

    # Only a concrete catalog unit is comparable. An abstract token is handled by V2/V3.
    if (
        catalog_prop.unit is not None
        and catalog_prop.unit not in ABSTRACT_UNITS
        and prop.unit != catalog_prop.unit
    ):
        findings.append(
            _observation(
                "O3",
                Bucket.DIVERGENCE,
                f"unit {prop.unit!r} differs from catalog {catalog_prop.unit!r}",
                device.id,
                node.id,
                prop.id,
            )
        )

    if prop.settable != catalog_prop.settable:
        findings.append(
            _observation(
                "O4",
                Bucket.DIVERGENCE,
                f"settable={prop.settable} differs from catalog settable={catalog_prop.settable}",
                device.id,
                node.id,
                prop.id,
            )
        )

    if prop.unit is not None and prop.datatype not in _NUMERIC_DATATYPES:
        findings.append(
            _observation(
                "O5",
                Bucket.DIVERGENCE,
                f"unit {prop.unit!r} on non-numeric datatype '{prop.datatype}'",
                device.id,
                node.id,
                prop.id,
            )
        )

    return findings


def check_observations(tree: HomieTree, catalogs: dict[str, Catalog]) -> list[Finding]:
    """Legal divergence, extension and omission. Never fails a build."""
    findings: list[Finding] = []

    for device in tree.devices.values():
        for node in device.nodes.values():
            if node.type is None or not node.type.startswith(_EBUS_CAPABILITY_PREFIX):
                findings.append(
                    _observation(
                        "O7",
                        Bucket.EXTENSION,
                        f"node type {node.type!r} is not an eBus capability; published as "
                        "an extension, which the specification permits",
                        device.id,
                        node.id,
                    )
                )
                continue

            catalog = catalogs.get(node.type)
            if catalog is None:
                findings.append(
                    _observation(
                        "O1",
                        Bucket.DIVERGENCE,
                        f"node type '{node.type}' names no capability we vendored",
                        device.id,
                        node.id,
                    )
                )
                continue

            for prop in node.properties.values():
                findings.extend(_observe_property(device, node, prop, catalog))

            for catalog_prop_id in catalog.properties:
                if catalog_prop_id not in node.properties:
                    findings.append(
                        _observation(
                            "O9",
                            Bucket.OMISSION,
                            f"catalog property '{catalog_prop_id}' is not published; "
                            "publishing is opt-in, so this records coverage rather than "
                            "a defect",
                            device.id,
                            node.id,
                            catalog_prop_id,
                        )
                    )

    return findings


def check_profile_coverage(tree: HomieTree, profiles: dict[str, DeviceProfile]) -> list[Finding]:
    """O8: capabilities a device type's profile composes but the device does not publish.

    An omission, never a violation. A profile's ``req`` is capability-level *guidance*
    with a MAY default, and publishing is opt-in, so an absent capability is a fact about
    this device rather than a defect.
    """
    findings: list[Finding] = []
    for device in tree.devices.values():
        if device.type is None:
            continue
        profile = profiles.get(device.type)
        if profile is None:
            continue
        for node_id, capability in profile.capabilities.items():
            if node_id not in device.nodes:
                findings.append(
                    _observation(
                        "O8",
                        Bucket.OMISSION,
                        f"profile for '{device.type}' composes '{capability}' as node "
                        f"'{node_id}', which this device does not publish",
                        device.id,
                        node_id,
                    )
                )
    return findings
