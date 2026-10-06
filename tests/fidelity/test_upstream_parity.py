"""Does panelbench publish what the reference eBus emitter publishes?

The conformance suite next door answers a different question. It asks whether
everything panelbench publishes is *legal*, and the specification permits
omission — which is why it can report ``conformant: true`` alongside 1,486
omissions without contradicting itself. Nothing measured fidelity until this.

The two producers no longer read one input, so each cell hands both of them the
same *panel* by a different route:

==============  ==============================  ==================================
cell            reference (upstream's emitter)  subject (PanelBench)
==============  ==============================  ==================================
``example``     upstream's shipped definition   PanelBench's import of that
                and ticks                       definition, run as a clone
``panelbench``  upstream's capture reading      ``configs/default_MAIN_40.yaml``
                PanelBench's published tree
==============  ==============================  ==================================

The **example** cell is the sharpest claim: upstream's own definition, read both
ways, so any asymmetry is a producer difference rather than a difference in what
the two were told. It is a SPAN panel with two PV inverters, each fed by its own
commissioned ``Commissioned PV System`` circuit, a never-backup pool pump, a
battery with its MID and two SPAN Drives. PanelBench reads it through the same
translator it reads a live panel with, so the cell also measures the clone: that
it keeps the panel's firmware and hardware, commissions both circuits, and gives
each inverter its own device and identity.

The **panelbench** cell runs PanelBench's own richest config — every circuit
template, both EVSEs, PV, tandem breakers — and lets upstream's capture read the
published tree back into a definition, which upstream's emitter then publishes.
It is the only cell that exercises PanelBench's PV path from a PanelBench config.

**The panelbench cell is at full structural parity**, so it has no baseline: it
asserts an empty report directly, and any gap is a regression rather than
something to record.

The example cell keeps one, for a single entry: the battery's ``info/model``.
Upstream's example names its battery ``Example BESS``; PanelBench's clone carries
a battery's capacity but none of its identity, so the imported battery publishes
no model. That is a clone gap, not a difference between the emitters, and the
entry leaves when the clone carries a battery's identity as it carries each PV
inverter's.

A baseline exists to make a known divergence exact while it is being closed, so
**any** movement fails: a new gap appearing, or a known one closing. Both deserve
a deliberate look. When a baseline reaches empty, delete it and assert parity
directly, as the panelbench cell does.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from .comparator import (
    PANELBENCH_CONFIG,
    UPSTREAM,
    Capture,
    ParityReport,
    by_role,
    compare,
    compare_settable,
    declared_properties,
    example_pair,
    panelbench_pair,
    role_of,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Where a developer checkout of the emitter usually sits, so drift is caught
# locally. CI has no checkout and skips.
#
# This must be a checkout of the UPSTREAM repository, because the reference is
# upstream's file and drift means "upstream moved". It previously named
# `distribution-enclosure-simulator`, which was the SpanPanel fork — retired once
# the released wheel carried the fix it existed for. A fork checkout would answer
# a slightly different question even while it worked, and the retirement makes the
# distinction concrete: the fork is frozen, so it can never report drift again,
# and this check fails open (`pytest.skip`) rather than loudly.
_CONVENTIONAL_CHECKOUT = (
    Path.home() / "projects" / "ebus" / "distribution-enclosure-simulator-upstream"
)


@dataclass(frozen=True)
class Cell:
    """One comparison: how to produce (reference, PanelBench), and its baseline."""

    name: str
    pair: Callable[[Path], Awaitable[tuple[Capture, Capture]]]
    baseline: Path | None
    """``None`` once the cell reaches parity.

    A baseline exists to make a known divergence exact while it is being closed.
    An *empty* baseline file would be a weaker statement than its own absence: it
    reads as a place to record the next gap, where asserting parity directly says
    there is not supposed to be one.
    """


CELLS = (
    Cell("example", example_pair, FIXTURES / "parity_baseline_example.json"),
    Cell("panelbench", panelbench_pair, None),
)

VENDORED: dict[str, str] = {
    "forty_tab_minimal.yaml": "examples/forty_tab_minimal.yaml",
    "forty_tab_minimal.ticks.yaml": "examples/forty_tab_minimal.ticks.yaml",
}
"""Each vendored file under ``fixtures/upstream``, by its path in the pinned release."""

_by_name = pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.name)


@_by_name
@pytest.mark.asyncio
async def test_structural_parity_matches_the_recorded_baseline(cell: Cell, tmp_path: Path) -> None:
    """The instrument. Fails on any structural movement in either direction.

    Extras that the reference *declares* and merely leaves unvalued are filtered
    out first, for the reason `test_panelbench_publishes_nothing_the_reference_does_not`
    states at length: `compare` diffs published values, and the reference is
    upstream's example, so its identity values are as thin as an example needs.
    Panelbench valuing `info/firmware-version` on a device whose profile both
    producers share is fidelity to the firmware, not structural drift. The filter
    is a no-op wherever the reference values what it declares.
    """
    reference, subject = await cell.pair(tmp_path)
    report = _without_declared_extensions(compare(reference, subject), reference)

    if cell.baseline is None:
        assert report.as_baseline() == ParityReport().as_baseline(), (
            f"the {cell.name} cell was at full structural parity and no longer is.\n"
            f"{report.describe()}\n\n"
            "This is a producer regression, not a baseline to update."
        )
        return

    expected = json.loads(cell.baseline.read_text())
    assert report.as_baseline() == expected, (
        f"structural parity with the reference emitter moved for the {cell.name} cell.\n"
        f"{report.describe()}\n\n"
        f"If this is a gap you closed, update {cell.baseline.name} to match. "
        "If it is a gap that appeared, it is a producer regression."
    )


@_by_name
@pytest.mark.asyncio
async def test_panelbench_publishes_nothing_the_reference_does_not(
    cell: Cell, tmp_path: Path
) -> None:
    """Held separately from the baseline because the two failure modes differ.

    An omission is a missing feature. An *extension* is a claim about the eBus
    contract that the reference does not make, and the integration would build
    entities on it that firmware never sends — the orphan case, arrived at from
    the producer side. There are none today and there should not be any.

    "Does not make" means does not *declare*. `compare` diffs published values, and
    a property the reference declares but never values is not a claim panelbench
    invented — both producers agree the property exists, and only one has a value
    for it in this config. Valuing it is filling in a shared declaration, which is
    the opposite of an orphan: firmware sends it, and a consumer that builds an
    entity from it is right to. The reference is upstream's *example*, so its
    identity values are as thin as an example needs; its `$description` is not.

    So the guard is against a property absent from the reference's own
    `$description`. That is mechanically checkable from the capture, needs no
    allowlist to drift, and still fails on the case it was written for: a property
    panelbench publishes that the shared profile does not declare at all.
    """
    reference, subject = await cell.pair(tmp_path)
    report = compare(reference, subject)
    undeclared = _without_declared_extensions(report, reference)

    assert not report.extra_devices, f"devices absent from the reference: {report.extra_devices}"
    assert not undeclared.extra_properties, (
        "properties panelbench publishes that the reference does not even declare: "
        f"{undeclared.extra_properties}"
    )


@_by_name
@pytest.mark.asyncio
async def test_both_producers_agree_on_what_is_settable(cell: Cell, tmp_path: Path) -> None:
    """The commissioning locks, which structural parity cannot see.

    A lost never-backup or commissioned-system lock changes no property's existence,
    only whether a consumer is offered the write. Both producers in every cell are
    given the same locks, so a difference is a producer defect: fix it, never
    baseline it.
    """
    reference, subject = await cell.pair(tmp_path)

    assert compare_settable(reference, subject) == {}


def _without_declared_extensions(
    report: ParityReport, reference: dict[str, dict[str, str]]
) -> ParityReport:
    """Drop extras the reference declares but leaves unvalued.

    What remains in `extra_properties` is the orphan case: a property panelbench
    publishes that the shared profile does not declare at all. Nothing else about
    the report changes — omissions in particular are untouched, because a property
    the reference values and panelbench does not is a gap either way.
    """
    declared = {role: declared_properties(body) for role, body in by_role(reference).items()}
    remaining = {
        role: [p for p in props if p not in declared.get(role, set())]
        for role, props in report.extra_properties.items()
    }
    return ParityReport(
        missing_devices=report.missing_devices,
        extra_devices=report.extra_devices,
        missing_properties=report.missing_properties,
        extra_properties={role: props for role, props in remaining.items() if props},
    )


_COMMISSIONED_PV = "energy.ebus.device.circuit::Commissioned PV System"
_PV = "energy.ebus.device.pv::"


@pytest.mark.asyncio
async def test_the_example_reproduces_both_inverters_and_both_commissioned_circuits(
    tmp_path: Path,
) -> None:
    """Both inverters, and both circuits that feed them, through PanelBench's import.

    The two circuits share a role, so they are counted with multiplicity: that is
    what proves neither was lost.
    """
    reference, subject = await example_pair(tmp_path)

    def census(capture: Capture) -> Counter[str]:
        roles = (role_of(device_id, body) for device_id, body in capture.items())
        return Counter(r for r in roles if r == _COMMISSIONED_PV or r.startswith(_PV))

    expected = Counter({_COMMISSIONED_PV: 2, f"{_PV}Solar": 1, f"{_PV}Solar 2": 1})
    assert census(reference) == expected
    assert census(subject) == expected


def test_the_panelbench_config_still_exercises_pv() -> None:
    """The panelbench cell is the only one that publishes PanelBench's PV path from a
    PanelBench config, so the keys that drive it are load-bearing.

    Drop ``pv.enabled`` *and* every pv-typed circuit and PanelBench silently stops
    publishing PV, so the cell agrees perfectly about a device tree with no solar in
    it: upstream's reading of that tree has no inverter either. ``nameplate_capacity_w``
    is the quietest of the keys: without it PanelBench falls back to a default, which
    upstream then reads back, so losing it changes a published value with nothing,
    anywhere, disagreeing. ``display_name`` names the panel device both sides align
    by.

    A YAML comment cannot carry this warning: every config writer round-trips
    through ``yaml.dump``, which preserves unknown keys but drops comments.
    """
    config = yaml.safe_load(PANELBENCH_CONFIG.read_text())

    assert config["panel_config"].get("display_name"), (
        "both producers default the panel name differently, so an absent "
        "display_name makes the panel device fail to align by role"
    )
    assert (config.get("pv") or {}).get("enabled") is True, (
        "pv.enabled gates panelbench's PV device; without it PV survives only as "
        "long as a pv-typed circuit does"
    )
    templates = config.get("circuit_templates") or {}
    referenced = {c.get("template") for c in (config.get("circuits") or [])}
    solar = [
        name
        for name, template in templates.items()
        if template.get("device_type") == "pv" and name in referenced
    ]
    assert solar, (
        "no circuit resolves to a template with device_type: pv, so the panelbench "
        "cell would stop measuring solar"
    )
    assert all("nameplate_capacity_w" in templates[name] for name in solar), (
        "the solar template's nameplate_capacity_w feeds info/nominal-power-w; "
        "without it the published value falls to a default and no instrument disagrees"
    )


def test_vendored_example_matches_the_pinned_release() -> None:
    """The vendored data must be the pinned release's, byte for byte.

    Compared against the release tag rather than the checkout's working tree, so
    the check answers "does the fixture match what we pin" wherever the
    developer's checkout happens to be.
    """
    checkout = _emitter_checkout()
    if checkout is None:
        pytest.skip("no emitter checkout; set EBUS_EMITTER_CHECKOUT to enable the drift check")
    tag = f"v{importlib.metadata.version('ebus-panel-sim')}"
    for name, source in VENDORED.items():
        shown = subprocess.run(
            ["git", "-C", str(checkout), "show", f"{tag}:{source}"],
            capture_output=True,
            check=False,
        )
        if shown.returncode != 0:
            pytest.skip(f"{tag} is not in the checkout; fetch its tags to enable the drift check")
        assert (UPSTREAM / name).read_bytes() == shown.stdout, (
            f"{name} differs from {tag}:{source}. Re-copy it and review every fidelity "
            "baseline: a moved reference changes what fidelity means."
        )


def _emitter_checkout() -> Path | None:
    override = os.environ.get("EBUS_EMITTER_CHECKOUT")
    candidate = Path(override) if override else _CONVENTIONAL_CHECKOUT
    return candidate if (candidate / ".git").exists() else None
