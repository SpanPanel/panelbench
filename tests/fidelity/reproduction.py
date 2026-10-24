"""A reproduction of a reference capture, held to the bar at the captured instant.

Shared by the cells, which take each capture by both of PanelBench's routes, and by
the reference templates, which are the import route as a user meets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ebus_panel_sim import ReferenceCapture, Tree

from panelbench.emitter_adapter.runtime import _panel_envelope
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.emitter_adapter.wire_capture import recorded_panel

from .replay import replayed
from .tree_diff import differences, published_tree

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


async def published_at_the_captured_instant(path: Path, capture: ReferenceCapture) -> Tree:
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


async def assert_reproduces(path: Path, capture: ReferenceCapture, *, exactly: bool) -> None:
    """The panel *path* describes reproduces *capture* but for the emitter's exceptions.

    *exactly* also fails on a listed exception that no longer occurs, for a config that
    publishes the emitter's own definition, dispatch included, and so shows exactly
    what the emitter shows. A clone dispatches its battery by PanelBench's defaults,
    which no panel publishes, so a sign may come out either way there.
    """
    found = differences(capture.tree, await published_at_the_captured_instant(path, capture))

    allowed = EMITTER_EXCEPTIONS.get(capture.name, {})
    unexplained = [d for d in found if not any(d.startswith(prefix) for prefix in allowed)]
    assert not unexplained, f"{len(unexplained)} differences:\n" + "\n".join(unexplained)
    if exactly:
        stale = [prefix for prefix in allowed if not any(d.startswith(prefix) for d in found)]
        assert not stale, "listed differences that no longer occur:\n" + "\n".join(stale)
