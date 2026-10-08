"""SPAN panel hardware-model identifiers, and the model a panel reports.

These strings are SPAN-specific — the eBus Homie schema does not define a
panel-model enum, so values here are informational on the wire. ``panel_model``
is the one answer to which a panel reports: MQTT's ``info/model``, and the mDNS
record ``_start_panel`` registers, both ask it, so the two cannot disagree.

Inlined from the legacy ``engine.py`` so callers (``app.py`` HTTP bootstrap,
etc.) no longer need to import the now-deleted simulation engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
