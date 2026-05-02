"""SPAN panel hardware-model identifiers, keyed by total_tabs.

These strings are SPAN-specific — the eBus Homie schema does not define a
panel-model enum, so values here are informational on the wire. Used by the
bootstrap HTTP endpoint for the SPAN HA integration's panel-size badge and
emitted as the manifest ``panel-model`` metadata key.

Inlined from the legacy ``engine.py`` so callers (``app.py`` HTTP bootstrap,
etc.) no longer need to import the now-deleted simulation engine."""

from __future__ import annotations

PANEL_SIZE_TO_MODEL: dict[int, str] = {
    8: "MAIN_8",
    16: "MAIN_16",
    24: "MLO_24",
    32: "MAIN_32",
    40: "MAIN_40",
    48: "MLO_48",
}
