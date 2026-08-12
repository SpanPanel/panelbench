"""Inverter type as a fact of its own, separate from shed priority.

``priority`` used to carry two unrelated meanings on a producer template: how the
circuit should be shed, and whether its inverter is grid-forming. The dashboard's
*Inverter Type* selector wrote ``priority: MUST_HAVE`` for hybrid, ``OFF_GRID``
otherwise, and both ``engine.py`` and the emitter adapter read islandability back
out of that same field.

Two costs came from the conflation. Choosing *Hybrid* silently rewrote the
circuit's shed priority, which is then published to consumers as
``default-priority`` -- so an inverter setting changed a load-shedding answer. And
priority could not be set independently of inverter type at all: picking a shed
behaviour for a producer meant picking an inverter, and vice versa.

The two are now separate keys. ``priority`` means shedding and nothing else;
``inverter_type`` means grid-forming capability and nothing else.

Vocabulary is the wire's -- ``hybrid`` / ``ac-coupled`` -- so a template value
needs no translation before it is published as ``inverter-type``. Underscore forms
and the dashboard's ``grid_tied`` are accepted on input, because clones written
before this split use them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

HYBRID = "hybrid"
"""A grid-forming inverter: the site can island."""

AC_COUPLED = "ac-coupled"
"""A grid-following inverter: the site drops with the grid."""

LEGACY_HYBRID_PRIORITY = "MUST_HAVE"
"""What hybrid was encoded as before ``inverter_type`` existed."""

_ALIASES = {
    HYBRID: HYBRID,
    AC_COUPLED: AC_COUPLED,
    "grid-tied": AC_COUPLED,
}


def normalise_inverter_type(raw: str) -> str:
    """Coerce any accepted spelling to the wire vocabulary.

    Unrecognised values become ``ac-coupled`` rather than raising: an unknown
    inverter is not evidence of a grid-forming one, and defaulting the other way
    would have a panel claim it can island on the strength of a typo.
    """
    return _ALIASES.get(raw.strip().lower().replace("_", "-"), AC_COUPLED)


def template_inverter_type(template: Mapping[str, object]) -> str:
    """The inverter type a circuit template declares.

    Falls back to the legacy ``priority: MUST_HAVE`` encoding so clones written
    before the split keep working. The fallback reads priority but never writes it,
    so an old config is interpreted correctly and a new one is unambiguous.
    """
    declared = template.get("inverter_type")
    if declared is not None:
        return normalise_inverter_type(str(declared))
    if str(template.get("priority", "")).upper() == LEGACY_HYBRID_PRIORITY:
        return HYBRID
    return AC_COUPLED


def template_is_hybrid(template: Mapping[str, object] | None) -> bool:
    """Whether this template's inverter can form a grid, and so island."""
    return template is not None and template_inverter_type(template) == HYBRID
