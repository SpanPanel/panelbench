"""Does panelbench publish what the reference eBus emitter publishes?

The conformance suite next door answers a different question. It asks whether
everything panelbench publishes is *legal*, and the specification permits
omission — which is why it can report ``conformant: true`` alongside 1,486
omissions without contradicting itself. Nothing measured fidelity until this.

Both producers are run over **both** configs, so each config is an independent
comparison:

==================================  ==================  ====================
..                                  reference emitter   panelbench emitter
==================================  ==================  ====================
``forty_tab_minimal.yaml``          ⓐ                   ⓑ
``configs/default_MAIN_40.yaml``    ⓒ                   ⓓ
==================================  ==================  ====================

ⓐ↔ⓑ is the sharpest structural claim: a minimal surface, so any asymmetry is
unambiguously a producer defect. ⓒ↔ⓓ exercises far more of the tree — every
circuit template, both EVSEs, PV, tandem breakers — and reached gaps the minimal
config was too small to express. ``bess info/part-number`` was the worked example:
the minimal config carries no ``part_number``, so neither producer published one
and ⓐ↔ⓑ reported parity; the rich config does, ⓒ↔ⓓ named the gap, and adopting
the upstream emitter closed it.

**The rich cell is now at full structural parity**, so it has no baseline: it
asserts an empty report directly, and any gap is a regression rather than
something to record.

The minimal cell keeps one, for a single entry that is a config difference rather
than a producer defect. The reference hardcodes the PV model in its example
script; panelbench reads it from a ``pv:`` config block, and upstream's config has
nowhere to put one. Closing it would mean either editing a file a byte-drift test
pins, or inventing a vendor SKU in our source — so it is recorded instead.

A baseline exists to make a known divergence exact while it is being closed, so
**any** movement fails: a new gap appearing, or a known one closing. Both deserve
a deliberate look. When a baseline reaches empty, delete it and assert parity
directly, as the rich cell now does.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from .comparator import (
    PANELBENCH_CONFIG,
    REFERENCE_CONFIG,
    UPSTREAM,
    ParityReport,
    capture_panelbench,
    capture_reference,
    compare,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Where a developer checkout of the emitter usually sits, so drift is caught
# locally. CI has no checkout and skips.
_CONVENTIONAL_CHECKOUT = Path.home() / "projects" / "ebus" / "distribution-enclosure-simulator"


@dataclass(frozen=True)
class Cell:
    """One row of the matrix: one config, run through both producers."""

    name: str
    config: Path
    baseline: Path | None
    """``None`` once the cell reaches parity.

    A baseline exists to make a known divergence exact while it is being closed.
    An *empty* baseline file would be a weaker statement than its own absence: it
    reads as a place to record the next gap, where asserting parity directly says
    there is not supposed to be one.
    """


CELLS = (
    Cell("minimal", REFERENCE_CONFIG, FIXTURES / "parity_baseline_minimal.json"),
    Cell("rich", PANELBENCH_CONFIG, None),
)

_by_name = pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.name)


@_by_name
@pytest.mark.asyncio
async def test_structural_parity_matches_the_recorded_baseline(cell: Cell) -> None:
    """The instrument. Fails on any structural movement in either direction."""
    report = compare(capture_reference(cell.config), await capture_panelbench(cell.config))

    if cell.baseline is None:
        assert report.as_baseline() == ParityReport().as_baseline(), (
            f"the {cell.name} config was at full structural parity and no longer is.\n"
            f"{report.describe()}\n\n"
            "This is a producer regression, not a baseline to update."
        )
        return

    expected = json.loads(cell.baseline.read_text())
    assert report.as_baseline() == expected, (
        f"structural parity with the reference emitter moved for the {cell.name} config.\n"
        f"{report.describe()}\n\n"
        f"If this is a gap you closed, update {cell.baseline.name} to match. "
        "If it is a gap that appeared, it is a producer regression."
    )


@_by_name
@pytest.mark.asyncio
async def test_panelbench_publishes_nothing_the_reference_does_not(cell: Cell) -> None:
    """Held separately from the baseline because the two failure modes differ.

    An omission is a missing feature. An *extension* is a claim about the eBus
    contract that the reference does not make, and the integration would build
    entities on it that firmware never sends — the orphan case, arrived at from
    the producer side. There are none today and there should not be any.
    """
    report = compare(capture_reference(cell.config), await capture_panelbench(cell.config))

    assert not report.extra_devices, f"devices absent from the reference: {report.extra_devices}"
    assert not report.extra_properties, (
        f"properties absent from the reference: {report.extra_properties}"
    )


@_by_name
@pytest.mark.asyncio
async def test_both_producers_read_the_same_config_file(cell: Cell) -> None:
    """Guards the premise of the whole comparison.

    A cell is only evidence about the producers if the input is identical. If
    either side ever needs its own copy, every difference above becomes ambiguous
    between a producer defect and a config difference, and the instrument
    silently stops measuring what it claims to.
    """
    assert cell.config.is_file()

    reference = capture_reference(cell.config)
    subject = await capture_panelbench(cell.config)

    assert reference, "reference emitter published nothing"
    assert subject, "panelbench published nothing"


def test_the_shared_config_still_carries_what_the_reference_needs() -> None:
    """The superset keys are load-bearing but invisible to panelbench.

    ``ticks``, ``islandable`` and the BESS identity block drive the *reference*
    side of the rich cell; panelbench ignores every one of them. Nothing in this
    package would notice their removal, and the cell would quietly degrade rather
    than fail — a MID that stops being expected, or a producer publishing nothing
    because it was handed no ticks.

    The PV keys are here for the opposite reason: both producers read them, and
    *asymmetrically*. The reference's only PV gate is a circuit whose template
    declares ``device_type: pv`` (``run_forty_tab_minimal.py:358``); panelbench
    also accepts ``pv.enabled`` (``spec_generator.py:232``). Drop one and parity
    catches it as an extra device — but drop **both** and each producer silently
    stops publishing PV, so the cell agrees perfectly about a device tree with no
    solar in it. ``nameplate_capacity_w`` is the quietest of the three: both sides
    fall back to the same 5000.0 default, so losing it changes a published value
    with nothing, anywhere, disagreeing.

    This matters more since ``default_MAIN_40`` became the superset config: it is
    the only cell that exercises PV at all, so its coverage is not recoverable
    from another template.

    A YAML comment cannot carry this warning: every config writer round-trips
    through ``yaml.dump``, which preserves unknown keys but drops comments.
    """
    config = yaml.safe_load(PANELBENCH_CONFIG.read_text())

    assert config["panel_config"].get("islandable") is True, (
        "islandable gates the reference's MID; without it the rich baseline's "
        "missing MID device silently stops being measured"
    )
    assert config["panel_config"].get("display_name"), (
        "both producers default the panel name differently, so an absent "
        "display_name makes the panel device fail to align by role"
    )
    missing = sorted(
        key
        for key in ("instance_id", "vendor", "product_name", "part_number", "serial_number")
        if key not in config["bess"]
    )
    assert not missing, f"BESS identity keys the reference reads are gone: {missing}"

    ticks = config.get("ticks")
    assert ticks, "the reference runner publishes nothing without a ticks block"
    assert all("circuits" in tick for tick in ticks), "each tick needs a circuits mapping"
    assert any(tick.get("grid_online") is False for tick in ticks), (
        "no tick takes the grid down, so the MID's islanded branch — the one "
        "where grid-forming-entity has to name a device rather than say GRID — "
        "is never reached by anything in this repo (see test_islanded_wire.py)"
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
        "no circuit resolves to a template with device_type: pv — this is the "
        "reference's only PV gate, so the rich cell would stop measuring solar"
    )
    assert all("nameplate_capacity_w" in templates[name] for name in solar), (
        "the solar template's nameplate_capacity_w feeds info/nominal-power-w on "
        "both sides; without it both fall to the same 5000.0 default and no "
        "instrument disagrees"
    )


def test_vendored_reference_matches_upstream() -> None:
    """The vendored copies must stay byte-identical to the emitter's own files.

    Skipped without a local checkout, so CI stays self-contained — the cost is
    that drift is caught on a developer machine rather than in CI, which is the
    price of the examples not being in the wheel.
    """
    checkout = _emitter_checkout()
    if checkout is None:
        pytest.skip("no emitter checkout; set EBUS_EMITTER_CHECKOUT to enable the drift check")

    for name in ("forty_tab_minimal.yaml", "run_forty_tab_minimal.py"):
        vendored = (UPSTREAM / name).read_bytes()
        current = (checkout / "examples" / name).read_bytes()
        assert vendored == current, (
            f"{name} has drifted from the emitter checkout. Re-copy it and review "
            "the parity baseline: a moved reference changes what fidelity means."
        )


def _emitter_checkout() -> Path | None:
    override = os.environ.get("EBUS_EMITTER_CHECKOUT")
    candidate = Path(override) if override else _CONVENTIONAL_CHECKOUT
    return candidate if (candidate / "examples").is_dir() else None
