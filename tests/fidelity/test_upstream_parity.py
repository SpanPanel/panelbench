"""Does panelbench publish what the reference eBus emitter publishes?

The conformance suite next door answers a different question. It asks whether
everything panelbench publishes is *legal*, and the specification permits
omission — which is why it can report ``conformant: true`` alongside 1,486
omissions without contradicting itself. Nothing measured fidelity until this.

The gap set is held in a committed baseline rather than asserted to be empty.
A permanently red test gets ignored, and there is real divergence today; a
baseline makes it exact, so that **any** movement fails — a new gap appearing, or
a known gap being closed. Both deserve a deliberate look, and closing one should
shrink the file rather than pass silently. When the baseline reaches empty, delete
it and assert parity directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .comparator import (
    REFERENCE_CONFIG,
    UPSTREAM,
    capture_panelbench,
    capture_reference,
    compare,
)

BASELINE = Path(__file__).parent / "fixtures" / "parity_baseline.json"

# Where a developer checkout of the emitter usually sits, so drift is caught
# locally. CI has no checkout and skips.
_CONVENTIONAL_CHECKOUT = Path.home() / "projects" / "ebus" / "distribution-enclosure-simulator"


def _emitter_checkout() -> Path | None:
    override = os.environ.get("EBUS_EMITTER_CHECKOUT")
    candidate = Path(override) if override else _CONVENTIONAL_CHECKOUT
    return candidate if (candidate / "examples").is_dir() else None


@pytest.mark.asyncio
async def test_structural_parity_matches_the_recorded_baseline() -> None:
    """The instrument. Fails on any structural movement in either direction."""
    report = compare(capture_reference(), await capture_panelbench())
    expected = json.loads(BASELINE.read_text())

    assert report.as_baseline() == expected, (
        "structural parity with the reference emitter moved.\n"
        f"{report.describe()}\n\n"
        f"If this is a gap you closed, update {BASELINE.name} to match. "
        "If it is a gap that appeared, it is a producer regression."
    )


@pytest.mark.asyncio
async def test_panelbench_publishes_nothing_the_reference_does_not() -> None:
    """Held separately from the baseline because the two failure modes differ.

    An omission is a missing feature. An *extension* is a claim about the eBus
    contract that the reference does not make, and the integration would build
    entities on it that firmware never sends — the orphan case, arrived at from
    the producer side. There are none today and there should not be any.
    """
    report = compare(capture_reference(), await capture_panelbench())

    assert not report.extra_devices, f"devices absent from the reference: {report.extra_devices}"
    assert not report.extra_properties, (
        f"properties absent from the reference: {report.extra_properties}"
    )


@pytest.mark.asyncio
async def test_both_producers_read_the_same_config_file() -> None:
    """Guards the premise of the whole comparison.

    ⓐ↔ⓑ is only evidence about the producers if the input is identical. If
    panelbench ever needs its own copy of the reference config, every difference
    below becomes ambiguous between a producer defect and a config difference,
    and the instrument silently stops measuring what it claims to.
    """
    assert REFERENCE_CONFIG.is_file()

    reference = capture_reference(REFERENCE_CONFIG)
    subject = await capture_panelbench(REFERENCE_CONFIG)

    assert reference, "reference emitter published nothing"
    assert subject, "panelbench published nothing"


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
