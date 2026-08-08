"""Conformance checking of a published Homie 5 tree against the vendored eBus catalogs.

Independent of this simulator by design: see tests/conformance/test_boundary.py.
"""

from __future__ import annotations

from .catalogs import Catalog, CatalogError, load_catalogs
from .device_profiles import DeviceProfile, ProfileError, load_device_profiles
from .emitter_data import emitter_catalogs, emitter_profiles
from .feeds import CaptureError, from_capture, from_devices
from .model import DescriptionError, HomieTree, build_tree
from .report import ConformanceReport, build_report, render_json, render_text
from .rules import (
    Bucket,
    Finding,
    Severity,
    check_observations,
    check_profile_coverage,
    check_violations,
)

__all__ = [
    "Bucket",
    "CaptureError",
    "Catalog",
    "CatalogError",
    "ConformanceReport",
    "DescriptionError",
    "DeviceProfile",
    "Finding",
    "HomieTree",
    "ProfileError",
    "Severity",
    "build_report",
    "build_tree",
    "check",
    "check_observations",
    "check_profile_coverage",
    "check_violations",
    "emitter_catalogs",
    "emitter_profiles",
    "from_capture",
    "from_devices",
    "load_catalogs",
    "load_device_profiles",
    "render_json",
    "render_text",
]


def check(
    tree: HomieTree,
    catalogs: dict[str, Catalog],
    profiles: dict[str, DeviceProfile],
) -> ConformanceReport:
    """Run every rule and aggregate into a report."""
    findings = [
        *check_violations(tree, catalogs),
        *check_observations(tree, catalogs),
        *check_profile_coverage(tree, profiles),
    ]
    flagged: set[tuple[str, str, str]] = {
        (f.device, f.node, f.property)
        for f in findings
        if f.node is not None and f.property is not None
    }
    matches = 0
    for device in tree.devices.values():
        for node in device.nodes.values():
            catalog = catalogs.get(node.type) if node.type is not None else None
            if catalog is None:
                continue
            matches += sum(
                1
                for prop_id in node.properties
                if prop_id in catalog.properties and (device.id, node.id, prop_id) not in flagged
            )
    return build_report(findings, match_count=matches)
