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
(``reproduction.EMITTER_EXCEPTIONS``), each listed with its reason; on the import
route, which publishes the emitter's own definition, a listed difference that no
longer occurs fails too, so the list never outlives its reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from ebus_panel_sim import load_reference_capture, reference_capture_names

from panelbench.clone import translate_panel_tree
from panelbench.config_types import SimulationConfig
from panelbench.definition_import import config_from_definition
from panelbench.emitter_adapter.wire_capture import discovered_devices
from panelbench.hardware import panel_variant
from tests._helpers import write_config

from .reproduction import EMITTER_EXCEPTIONS, assert_reproduces
from .tree_diff import retained_topics

ROUTES: Final = ("clone", "import")


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


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "route"), _cells())
async def test_panelbench_reproduces_the_capture(name: str, route: str, tmp_path: Path) -> None:
    path = write_config(tmp_path / f"{name}.yaml", _config(name, route))

    await assert_reproduces(path, load_reference_capture(name), exactly=route == "import")
