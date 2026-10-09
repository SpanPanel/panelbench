"""PanelBench reproduces every reference capture the pinned emitter ships.

Each reference capture is a masked tree a real SPAN panel published, shipped with
the panel definition and ticks recorded with it (``load_reference_capture``). Each
is a cell, taken by both of PanelBench's routes to a config:

- **clone**: the captured tree read as a live panel's is, by ``translate_panel_tree``;
- **import**: the capture's definition read by PanelBench's definition import, as the
  dashboard's Import reads one.

PanelBench publishes the config and the published tree is held to the fidelity bar
(``tree_diff``) against the capture. A cell passes when nothing differs but what the
emitter itself cannot reproduce from the same definition (``EMITTER_EXCEPTIONS``),
each listed with its reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from ebus_panel_sim.reference_captures import load_reference_capture, reference_capture_names

from panelbench.clone import translate_panel_tree
from panelbench.definition_import import config_from_definition
from panelbench.emitter_adapter.wire_capture import capture_retained, discovered_devices
from tests._helpers import write_config

from .tree_diff import differences, published_tree, retained_topics

ROUTES: Final = ("clone", "import")

_FEEDTHROUGH: Final = (
    "The panel feeds a sub-panel through its downstream lugs (2700.6 W in the capture), "
    "which a definition cannot express, so the emitter's site lacks that load and its grid "
    "and upstream lugs run the other way."
)

EMITTER_EXCEPTIONS: Final[dict[str, dict[str, str]]] = {
    "r202639-b": {
        "distribution-enclosure #1: power-flows/site sign": (
            "The panel's site is its load circuits plus the upstream load its meter outside "
            "the panel measures on the service conductor, as release 202639 computes it. "
            "The emitter's meter reads the panel's own grid power and its lugs carry only "
            "its circuits, so it measures no upstream load and its site stays the circuits' "
            "small load."
        ),
    },
    "main32_r202639": {
        "distribution-enclosure #1: power-flows/grid sign": _FEEDTHROUGH,
        "lugs UPSTREAM: meter/active-power sign": _FEEDTHROUGH,
    },
    "main32_r202633": {
        f"lugs UPSTREAM: connection/fed-by-device-{key}: valued only by the capture": (
            "The upstream lugs are fed by another enclosure, which a definition cannot express."
        )
        for key in ("id", "status", "type")
    },
}
"""What the emitter cannot reproduce from a capture's own definition, by line prefix.

A difference the emitter shows publishing the definition itself is beyond any config
PanelBench could write, so it is allowed here with the emitter's reason.
"""

_STANDING: Final[dict[tuple[str, str], str]] = {
    ("main32_r202633", "clone"): "29 differences",
    ("main32_r202633", "import"): "28 differences",
    ("main32_r202639", "clone"): "2 differences",
    ("main32_r202639", "import"): "2 differences",
    ("r202639-a", "clone"): "58 differences",
    ("r202639-a", "import"): "58 differences",
    ("r202639-b", "clone"): "publishing fails: an UNKNOWN shed priority is copied verbatim",
    ("r202639-b", "import"): "publishing fails: an UNKNOWN shed priority is copied verbatim",
    ("r202639-c", "clone"): "107 differences",
    ("r202639-c", "import"): "107 differences",
    ("r202639-d", "clone"): "134 differences",
    ("r202639-d", "import"): "importing fails: a battery without a nameplate is dropped",
    ("r202639-e", "clone"): "publishing fails: circuits sharing a space get one id",
    ("r202639-e", "import"): "publishing fails: circuits sharing a space get one id",
}
"""Each cell PanelBench does not yet reproduce, with what it shows, until it does."""


def _cells() -> Iterator[object]:
    for name in reference_capture_names():
        for route in ROUTES:
            standing = _STANDING.get((name, route))
            marks = [] if standing is None else [pytest.mark.xfail(strict=True, reason=standing)]
            yield pytest.param(name, route, marks=marks, id=f"{name}-{route}")


def _config(name: str, route: str) -> dict[str, object]:
    capture = load_reference_capture(name)
    if route == "import":
        return config_from_definition(capture.definition)
    [panel] = [i for i, device in capture.tree.items() if device.type == "distribution-enclosure"]
    return translate_panel_tree(panel, discovered_devices(retained_topics(capture.tree)))


def test_every_reference_capture_is_a_cell() -> None:
    """Guards the premise: the cells are the pinned release's captures, all of them."""
    names = reference_capture_names()

    assert names
    assert {name for name, _route in _STANDING} <= set(names)
    assert set(EMITTER_EXCEPTIONS) <= set(names)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "route"), _cells())
async def test_panelbench_reproduces_the_capture(name: str, route: str, tmp_path: Path) -> None:
    capture = load_reference_capture(name)
    path = write_config(tmp_path / f"{name}.yaml", _config(name, route))

    found = differences(capture.tree, published_tree(await capture_retained(path)))

    allowed = EMITTER_EXCEPTIONS.get(name, {})
    unexplained = [d for d in found if not any(d.startswith(prefix) for prefix in allowed)]
    assert not unexplained, f"{len(unexplained)} differences:\n" + "\n".join(unexplained)
