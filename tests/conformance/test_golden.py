"""The captured tree is the contract this simulator offers a consumer.

test_golden_report_is_unchanged fails on ANY change to the published surface, including
a perfectly legal one. That is deliberate: a legal change should be a reviewed edit to
the expected report, not something that slips through because no rule covered it.
"""

from __future__ import annotations

import json
from pathlib import Path

from panelbench.conformance import (
    ConformanceReport,
    build_tree,
    check,
    emitter_catalogs,
    emitter_profiles,
    from_capture,
    load_catalogs,
    load_device_profiles,
    render_json,
)

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGS = emitter_catalogs()
PROFILES = emitter_profiles()


def _report() -> ConformanceReport:
    tree = build_tree(from_capture(FIXTURES / "golden_tree.json"))
    return check(tree, load_catalogs(CATALOGS), load_device_profiles(PROFILES))


def test_golden_tree_has_no_violations() -> None:
    report = _report()
    assert report.conformant, [
        f"{f.rule} {f.device}/{f.node}/{f.property}: {f.message}" for f in report.violations
    ]


def test_golden_report_is_unchanged() -> None:
    expected = json.loads((FIXTURES / "golden_report.json").read_text())
    actual = json.loads(render_json(_report()))
    assert actual == expected, (
        "the published surface changed. If the change is intended, regenerate with:\n"
        "  uv run scripts/check-conformance.py "
        "--capture tests/conformance/fixtures/golden_tree.json --json "
        "> tests/conformance/fixtures/golden_report.json"
    )
