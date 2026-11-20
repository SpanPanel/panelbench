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

A panel publishes only which spaces are occupied, so the ranges are the client's. Two
rest on reference captures: MAIN_16's 9-24 on one whose 16 circuits sit on 9 to 24,
and MAIN_40's 9-48 on the release 202645 capture of a MAIN 40, whose 38 circuits sit
on 9 to 48."""


def panel_model(config: SimulationConfig) -> str:
    """The model the panel *config* describes, wherever the panel reports it.

    The config's own ``model``, as a clone keeps its panel's, else the model its
    size names. ``str()`` because YAML reads an all-digit value as a number.
    """
    return reported_model(config["panel_config"])


def reported_model(panel: Mapping[str, object]) -> str:
    """The model a ``panel_config`` mapping describes, as ``panel_model`` gives it."""
    model = panel.get("model")
    if model:
        return str(model)
    size = _total_tabs(panel)
    return PANEL_SIZE_TO_MODEL.get(size, f"MAIN_{size}")


def model_positions(model: str) -> tuple[int, int] | None:
    """*model*'s first and last space, read as the integration's client reads a model
    (stripped, upper case), or ``None`` for a model ``PANEL_POSITIONS`` does not hold."""
    return PANEL_POSITIONS.get(model.strip().upper())


def panel_spaces(panel: Mapping[str, object]) -> range:
    """The breaker spaces the ``panel_config`` mapping *panel* describes, in order.

    Where *panel* names its ``model``, as a clone and a reference template do, that
    model's positions (``PANEL_POSITIONS``) up to ``total_tabs``, so a circuit placed on
    one sits where the integration expects that model's circuits, even in a clone sized
    before PanelBench knew them. Otherwise 1 to ``total_tabs``: a model the table does
    not hold, or a config with no ``model``, sized by its count of spaces, which keeps
    the spaces it was written with.
    """
    total_tabs = _total_tabs(panel)
    model = panel.get("model")
    positions = model_positions(str(model)) if model else None
    if positions is not None:
        return range(positions[0], min(positions[1], total_tabs) + 1)
    return range(1, total_tabs + 1)


def _total_tabs(panel: Mapping[str, object]) -> int:
    raw = panel.get("total_tabs", DEFAULT_PANEL_SIZE)
    return int(raw) if isinstance(raw, int | float | str) else DEFAULT_PANEL_SIZE
