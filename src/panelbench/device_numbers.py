"""SPAN's number for each of a panel's circuits, and the id segment they publish under.

A hardware 3.0 panel numbers its circuits, and the meters outside it, and keeps each
number across releases while the id around it changes: ``circuit-<n>`` and
``remote-ct-<n>`` before release 202645, ``{serial}-{hex16}-1-16-<n>`` and
``{serial}-{hex16}-1-42-<n>`` from it, every id of one panel sharing one 16-hex
segment. A config records the number of each (``circuit_number``) and the segment
(``panel_config.device_id_hex``), read here from the ids a panel publishes, so the
ids PanelBench publishes follow the release as the panel's do
(``emitter_adapter.instance_ids``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# A circuit's or remote CT's id, either release's, with its number in one group.
_NUMBERED: Final = re.compile(r"(?:circuit|remote-ct)-(\d+)|.+-1-(?:16|42)-(\d+)")


def number_of(device_id: str) -> int | None:
    """SPAN's number for the circuit or remote CT *device_id* names, or None for an
    id that carries none: a MAIN 32's, the lugs', or PanelBench's own hashed one."""
    match = _NUMBERED.fullmatch(device_id)
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def is_remote_ct(device_id: str) -> bool:
    """Whether *device_id* names a meter outside the panel: ``remote-ct-<n>``, or
    ``…-1-42-<n>`` from release 202645."""
    return re.fullmatch(r"remote-ct-\d+|.+-1-42-\d+", device_id) is not None


def hex_of(root_id: str, device_ids: Iterable[str]) -> str | None:
    """The 16-hex segment the panel *root_id* publishes its numbered devices under, as
    the first of *device_ids* shaped ``{root_id}-{hex16}-1-<trait>-<n>`` has it; None
    where none is, as before release 202645."""
    shaped = re.compile(rf"{re.escape(root_id)}-([0-9a-f]{{16}})-1-\d+-\d+")
    for device_id in device_ids:
        if (match := shaped.fullmatch(device_id)) is not None:
            return match.group(1)
    return None


def next_number(config: Mapping[str, object]) -> int | None:
    """The lowest number above 0 none of *config*'s circuits uses, for a circuit added
    to it; None on a panel that numbers none of its devices. A remote CT's number is
    no circuit's: SPAN numbers the two apart."""
    used = {
        number
        for entry in _listed(config.get("circuits"))
        if isinstance(number := entry.get("circuit_number"), int)
    }
    numbered = used or any(
        isinstance(entry.get("circuit_number"), int)
        for entry in _listed(config.get("outside_meters"))
    )
    if not numbered:
        return None
    number = 1
    while number in used:
        number += 1
    return number


def _listed(value: object) -> list[Mapping[str, object]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []
