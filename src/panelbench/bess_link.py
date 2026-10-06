"""Health of the panel's link to its battery, as a producer reports it.

The values are the emitter's ``BESSCommunication`` — the ``status`` catalog's
``communication-state`` enum. Kept in one place so the engine, which stores the
requested health, and the dashboard, which validates what a user sends, agree
on what is legal without either re-deriving it.
"""

from __future__ import annotations

from typing import Final, TypeIs, get_args

from ebus_panel_sim import BESSCommunication

BESS_LINKS: Final[frozenset[str]] = frozenset(get_args(BESSCommunication))


def is_bess_link(value: object) -> TypeIs[BESSCommunication]:
    """Whether *value* is a link health the emitter accepts."""
    return value in BESS_LINKS
