"""The wire fixture — the artifact consumers are exercised against.

`golden_tree.json` holds `$description` documents and answers "is what we declare
legal?". This holds the whole retained surface and answers a different question:
can a consumer be driven end to end by what we publish? A parser fed only
declarations can be checked for understanding the shape of a panel, never for
building the right snapshot from one.

**Structure is asserted, never values.** The config carries `noise_factor` and the
clock advances, so power and current differ every run. `golden_report.json` is
compared exactly because a conformance profile must not drift silently; this one
cannot be, and pretending otherwise would produce a test that fails for reasons
nobody can act on.

The capture code lives in `emitter_adapter`, not in `conformance`, because taking
it means running *this* simulator, while the conformance rules describe any eBus
publisher's output — a boundary `test_boundary.py` enforces. The test sits here
because the fixture it guards does.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from panelbench.emitter_adapter.wire_capture import as_capture, capture

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_WIRE = _FIXTURES / "golden_wire.json"
_TREE = _FIXTURES / "golden_tree.json"
_CONFIG = _REPO / "configs" / "default_MAIN_40.yaml"


def _committed() -> dict[str, dict[str, str]]:
    with _WIRE.open() as handle:
        loaded: dict[str, dict[str, str]] = json.load(handle)
    return loaded


def test_every_captured_device_describes_itself() -> None:
    """A device with values but no description is unusable by a consumer: it has
    nothing to say what its properties mean."""
    undescribed = sorted(
        device for device, body in _committed().items() if "$description" not in body
    )

    assert not undescribed, f"devices published values but no $description: {undescribed}"


def test_every_captured_device_publishes_state() -> None:
    """Homie readiness is `$state`. A consumer waits on it, so a capture without
    it cannot drive a connection to completion."""
    stateless = sorted(device for device, body in _committed().items() if "$state" not in body)

    assert not stateless, f"devices published no $state: {stateless}"


def test_the_wire_capture_and_the_tree_capture_describe_the_same_panel() -> None:
    """Two fixtures, one panel. If they drift apart, one of them is describing a
    configuration nobody runs and the conformance profile stops applying to what
    consumers are tested against."""
    with _TREE.open() as handle:
        tree = json.load(handle)

    assert set(_committed()) == set(tree), (
        "the wire capture and the tree capture list different devices"
    )


def test_devices_carry_property_values_and_not_only_declarations() -> None:
    """The reason this fixture exists. A capture that lost its values would still
    pass every check above while being exactly as useless as the tree capture."""
    valued = {
        device: [key for key in body if not key.startswith("$")]
        for device, body in _committed().items()
    }
    empty = sorted(device for device, keys in valued.items() if not keys)

    assert not empty, f"devices declared properties but published no values: {empty}"
    assert sum(len(keys) for keys in valued.values()) > 100, (
        "implausibly few property values for a 40-space panel"
    )


@pytest.mark.asyncio
async def test_the_committed_capture_still_matches_what_the_emitter_emits() -> None:
    """Guards against the fixture going stale.

    Compares the *shape* — which devices, and which topics each publishes — and
    deliberately not the values. A property added, removed or renamed changes the
    key set and fails here; a different wattage does not.
    """
    fresh = as_capture(await _fresh_retained())
    committed = _committed()

    assert set(fresh) == set(committed), (
        "the emitter now publishes a different device set than the committed capture; "
        "rerun scripts/capture-wire.py"
    )

    differing = sorted(
        device for device in committed if set(committed[device]) != set(fresh[device])
    )
    assert not differing, (
        "these devices now publish different topics than the committed capture: "
        f"{differing}. Rerun scripts/capture-wire.py."
    )


async def _fresh_retained() -> dict[str, bytes]:
    """Re-run the capture and hand back the flat topic map.

    Goes through `capture()` so the test exercises the same path the script does,
    then re-flattens, because comparing at the topic level is what makes the
    "same shape" assertion above precise.
    """
    fresh = await capture(_CONFIG)
    return {
        f"ebus/5/{device}/{key}": value.encode()
        for device, body in fresh.items()
        for key, value in body.items()
    }
