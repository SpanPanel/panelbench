"""Every template places its circuits on the breaker spaces its model has.

A SPAN panel publishes which spaces are occupied, not where its model's spaces start,
and not every model starts at 1. The SpanPanel integration's client, span-panel-api,
holds each model's first and last space in schema-1's ``PANEL_POSITIONS_BY_MODEL``
(``packages/schema-1/src/span_panel_api_schema_1/const.py``) and warns of a panel whose
circuits sit outside them. The MAIN 16 and MAIN 40 templates this repository once
tracked numbered their spaces from 1, and that warning is what retired them.

What the ranges rest on differs. MAIN_16's 9-24 is forced by its reference capture,
whose circuits sit on 9 and 24 on a 16-space model. MAIN_40's 9-48 is the table's
choice by analogy: its capture's circuits sit on 9 to 45, which 6-45, 7-46, 8-47 and
9-48 all hold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import yaml

from panelbench.panel_models import panel_model
from tests._helpers import EVERY_TEMPLATE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from panelbench.config_types import SimulationConfig

# Copied from span-panel-api's schema-1 `PANEL_POSITIONS_BY_MODEL`, which PanelBench does
# not depend on: each model's first and last breaker space.
_POSITIONS: Final[Mapping[str, tuple[int, int]]] = {
    "MAIN_16": (9, 24),
    "MLO_24": (1, 24),
    "MAIN_32": (1, 32),
    "MAIN_40": (9, 48),
    "MLO_48": (1, 48),
}


def test_every_shipped_template_places_its_circuits_within_its_models_positions(
    template_dir: Path,
) -> None:
    """By the model each panel reports. One the table does not hold, as a capture's
    ``UNKNOWN``, has no positions to place against, so it is skipped, and the skips are
    asserted, so the test cannot pass by checking nothing."""
    outside: list[str] = []
    skipped: set[str] = set()
    for name in EVERY_TEMPLATE:
        config: SimulationConfig = yaml.safe_load(
            (template_dir / name).read_text(encoding="utf-8")
        )
        model = panel_model(config)
        if model not in _POSITIONS:
            skipped.add(name)
            continue
        first, last = _POSITIONS[model]
        outside.extend(
            f"{name}: {circuit['id']} on {circuit['tabs']}, outside {model}'s {first}-{last}"
            for circuit in config["circuits"]
            if not all(first <= tab <= last for tab in circuit["tabs"])
        )

    assert skipped == {"ref_UNKNOWN_16.yaml"}, skipped
    assert not outside, outside
