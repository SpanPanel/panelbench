from __future__ import annotations

import json

from panelbench.conformance.report import build_report, render_json, render_text
from panelbench.conformance.rules import Bucket, Finding, Severity

VIOLATION = Finding(
    rule="V3",
    severity=Severity.VIOLATION,
    bucket=Bucket.VIOLATION,
    message="no unit published",
    device="bess-1",
    node="soc",
    property="soe",
)
EXTENSION = Finding(
    rule="O6",
    severity=Severity.OBSERVATION,
    bucket=Bucket.EXTENSION,
    message="not in catalog",
    device="bess-1",
    node="soc",
    property="extra",
)


def test_report_counts_by_bucket() -> None:
    report = build_report([VIOLATION, EXTENSION], match_count=12)
    assert report.counts == {
        "match": 12,
        "divergence": 0,
        "extension": 1,
        "omission": 0,
        "violation": 1,
    }
    assert report.conformant is False


def test_report_is_conformant_with_observations_only() -> None:
    assert build_report([EXTENSION], match_count=3).conformant is True


def test_render_json_round_trips() -> None:
    parsed = json.loads(render_json(build_report([VIOLATION], match_count=1)))
    assert parsed["conformant"] is False
    assert parsed["violations"][0]["rule"] == "V3"
    assert parsed["violations"][0]["property"] == "soe"


def test_render_text_leads_with_the_verdict() -> None:
    text = render_text(build_report([VIOLATION], match_count=1))
    assert "1 violation" in text
    assert "bess-1/soc/soe" in text
