"""The conformance report: what this publisher emits, relative to the specification.

The report is the deliverable. Violations fail a build, but the classification -
match, divergence, extension, omission - is what a consumer author actually needs,
because it states the contract this producer offers and is derived from the wire
rather than from prose that drifts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .rules import Bucket, Finding, Severity


@dataclass(frozen=True)
class ConformanceReport:
    counts: dict[str, int]
    violations: tuple[Finding, ...]
    observations: tuple[Finding, ...]

    @property
    def conformant(self) -> bool:
        return not self.violations


def build_report(findings: list[Finding], match_count: int) -> ConformanceReport:
    counts = {bucket.value: 0 for bucket in Bucket}
    counts[Bucket.MATCH.value] = match_count
    for finding in findings:
        counts[finding.bucket.value] += 1
    return ConformanceReport(
        counts=counts,
        violations=tuple(f for f in findings if f.severity is Severity.VIOLATION),
        observations=tuple(f for f in findings if f.severity is Severity.OBSERVATION),
    )


def _path(finding: Finding) -> str:
    parts = [finding.device]
    if finding.node is not None:
        parts.append(finding.node)
    if finding.property is not None:
        parts.append(finding.property)
    return "/".join(parts)


def _as_dict(finding: Finding) -> dict[str, str | None]:
    return {
        "rule": finding.rule,
        "bucket": finding.bucket.value,
        "path": _path(finding),
        "device": finding.device,
        "node": finding.node,
        "property": finding.property,
        "message": finding.message,
    }


def render_json(report: ConformanceReport) -> str:
    """Machine-readable form. A consumer can assert against this directly."""
    return json.dumps(
        {
            "conformant": report.conformant,
            "counts": report.counts,
            "violations": [_as_dict(f) for f in report.violations],
            "observations": [_as_dict(f) for f in report.observations],
        },
        indent=2,
        sort_keys=True,
    )


def render_text(report: ConformanceReport, verbose: bool = False) -> str:
    lines: list[str] = []
    counts = report.counts
    lines.append(
        f"conformance: {counts['match']} match, {counts['divergence']} divergence, "
        f"{counts['extension']} extension, {counts['omission']} omission"
    )

    if report.violations:
        lines.append("")
        lines.append(f"{len(report.violations)} violation(s):")
        for finding in report.violations:
            lines.append(f"  {finding.rule}  {_path(finding)}: {finding.message}")
    else:
        lines.append("  no violations")

    # Omissions are the bulk of any real tree - a full BESS omits many optional
    # properties - so they stay behind --verbose rather than burying the violations.
    shown = [f for f in report.observations if verbose or f.bucket is not Bucket.OMISSION]
    if shown:
        lines.append("")
        lines.append("observations (never fatal):")
        for finding in shown:
            lines.append(f"  {finding.rule}  {_path(finding)}: {finding.message}")

    return "\n".join(lines)
