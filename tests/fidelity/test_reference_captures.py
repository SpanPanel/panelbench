"""PanelBench reproduces every reference capture the pinned emitter ships.

Each reference capture is a masked tree a real SPAN panel published, shipped with
the panel definition and ticks recorded with it (``load_reference_capture``). Each
is a cell, taken by both of PanelBench's routes to a config:

- **clone**: the captured tree read as a live panel's is, by ``translate_panel_tree``;
- **import**: the capture's definition read by PanelBench's definition import, as the
  dashboard's Import reads one.

PanelBench publishes the config at the captured instant, its first tick driven by
the readings the capture was recorded with (``replay``), and the published tree is
held to the fidelity bar (``tree_diff``) against the capture. A cell passes when
nothing differs but what the emitter itself cannot reproduce from the same definition
(``EMITTER_EXCEPTIONS``), each listed with its reason; on the import route, which
publishes the emitter's own definition, a listed difference that no longer occurs
fails too, so the list never outlives its reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from ebus_panel_sim import ReferenceCapture, Tree, load_reference_capture, reference_capture_names

from panelbench.clone import translate_panel_tree
from panelbench.config_types import SimulationConfig
from panelbench.definition_import import config_from_definition
from panelbench.emitter_adapter.runtime import _panel_envelope
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import discovered_devices, recorded_panel
from panelbench.hardware import panel_variant
from tests._helpers import write_config

from .replay import replayed
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


def _cells() -> Iterator[object]:
    for name in reference_capture_names():
        for route in ROUTES:
            yield pytest.param(name, route, id=f"{name}-{route}")


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
    assert set(EMITTER_EXCEPTIONS) <= set(names)


@pytest.mark.parametrize("name", reference_capture_names())
def test_panelbench_selects_the_variant_each_capture_publishes(name: str) -> None:
    """PanelBench selects a panel's variant by its hardware version string, as the
    emitter's capture reads it; the capture's definition names the variant it chose.
    Read through a clone, which keeps the string."""
    config: SimulationConfig = _config(name, "clone")

    assert panel_variant(config) == load_reference_capture(name).definition.variant


async def _published_at_the_captured_instant(path: Path, capture: ReferenceCapture) -> Tree:
    """The panel *path* describes, its first tick the capture's first recorded one."""
    runtime, recorder = await recorded_panel(path)
    config = runtime.engine.config
    runtime.emitter.publish_tick(
        replayed(
            capture.ticks[0],
            capture.definition.manifest,
            build_manifest(config),
            _panel_envelope(config),
        )
    )
    return published_tree(recorder.retained)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "route"), _cells())
async def test_panelbench_reproduces_the_capture(name: str, route: str, tmp_path: Path) -> None:
    capture = load_reference_capture(name)
    path = write_config(tmp_path / f"{name}.yaml", _config(name, route))

    found = differences(capture.tree, await _published_at_the_captured_instant(path, capture))

    allowed = EMITTER_EXCEPTIONS.get(name, {})
    unexplained = [d for d in found if not any(d.startswith(prefix) for prefix in allowed)]
    assert not unexplained, f"{len(unexplained)} differences:\n" + "\n".join(unexplained)
    if route == "import":
        # The import publishes the emitter's own definition, dispatch included, so it
        # shows exactly what the emitter shows. A clone dispatches its battery by
        # PanelBench's defaults, which no panel publishes, so a sign may come out
        # either way there.
        stale = [prefix for prefix in allowed if not any(d.startswith(prefix) for d in found)]
        assert not stale, "listed differences that no longer occur:\n" + "\n".join(stale)
