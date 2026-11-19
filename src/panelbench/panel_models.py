"""SPAN panel hardware-model identifiers, and the model a panel reports.

These strings are SPAN-specific — the eBus Homie schema does not define a
panel-model enum, so values here are informational on the wire. ``panel_model``
is the one answer to which a panel reports: MQTT's ``info/model``, and the mDNS
record ``_start_panel`` registers, both ask it, so the two cannot disagree.

Inlined from the legacy ``engine.py`` so callers (``app.py`` HTTP bootstrap,
etc.) no longer need to import the now-deleted simulation engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from panelbench.config_types import SimulationConfig

PANEL_SIZE_TO_MODEL: dict[int, str] = {
    8: "MAIN_8",
    16: "MAIN_16",
    24: "MLO_24",
    32: "MAIN_32",
    40: "MAIN_40",
    48: "MLO_48",
}

# The size a config naming none is taken as, by the manifest and the model alike.
DEFAULT_PANEL_SIZE = 40

PANEL_POSITIONS: Final[Mapping[str, tuple[int, int]]] = {
    "MAIN_16": (9, 24),
    "MLO_24": (1, 24),
    "MAIN_32": (1, 32),
    "MAIN_40": (9, 48),
    "MLO_48": (1, 48),
}
"""Each model's first and last breaker space, as the SpanPanel integration's client
holds them (span-panel-api, schema-1 ``PANEL_POSITIONS_BY_MODEL``) and warns of a panel
whose circuits sit outside them.

A panel publishes only which spaces are occupied, so the ranges are the client's. What
they rest on differs: MAIN_16's 9-24 is forced by its reference capture, whose 16
circuits sit on 9 to 24; MAIN_40's 9-48 is a choice by analogy, since its capture's
circuits sit on 9 to 45, which a numbering from 6, 7, 8 or 9 all hold."""


def panel_model(config: SimulationConfig) -> str:
    """The model the panel *config* describes, wherever the panel reports it.

    The config's own ``model``, as a clone keeps its panel's, else the model its
    size names. ``str()`` because YAML reads an all-digit value as a number.
    """
    panel = config["panel_config"]
    model = panel.get("model")
    if model:
        return str(model)
    size = int(panel.get("total_tabs", DEFAULT_PANEL_SIZE))
    return PANEL_SIZE_TO_MODEL.get(size, f"MAIN_{size}")


def panel_spaces(config: SimulationConfig) -> range:
    """The breaker spaces the panel *config* describes has, in order.

    Its reported model's positions (``PANEL_POSITIONS``), up to ``total_tabs``, so a
    circuit placed on one sits where the integration expects that model's circuits;
    1 to ``total_tabs`` for a model the table does not hold.
    """
    total_tabs = int(config["panel_config"].get("total_tabs", DEFAULT_PANEL_SIZE))
    first, last = PANEL_POSITIONS.get(panel_model(config), (1, total_tabs))
    return range(first, min(last, total_tabs) + 1)
