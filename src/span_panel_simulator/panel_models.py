"""SPAN panel hardware-model identifiers, keyed by total_tabs.

Inlined from the legacy ``engine.py`` so callers (``app.py`` HTTP bootstrap, etc.)
no longer need to import the now-deleted simulation engine."""

from __future__ import annotations

PANEL_SIZE_TO_MODEL: dict[int, str] = {
    16: "MAIN_16",
    24: "MLO_24",
    32: "MAIN_32",
    40: "MAIN_40",
    48: "MLO_48",
}
