"""The spaces a multi-load breaker's circuits may list: one rule, read by validation and
the clone.

Where a panel publishes one circuit per protected circuit (SPAN-API-Client-Docs#21), a
single-pole circuit lists its own space and a two-pole one lists both. A breaker then
fills one space, as a tandem does, or two on opposite legs, as a quad and a tandem-quad
do. Across two spaces it is a two-pole package, so at least one of its circuits spans
both. A space holds two of its circuits at most: a tandem's two single-pole circuits, a
quad's two two-pole ones, or a tandem-quad's single-pole one and its two-pole one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_CIRCUITS_PER_SPACE: Final = 2


def crowded_spaces(spaces: Mapping[str, frozenset[int]]) -> list[tuple[int, list[str]]]:
    """Each space more than ``MAX_CIRCUITS_PER_SPACE`` of the circuits *spaces* holds list,
    with those circuits' ids. *spaces* holds each circuit's spaces by its id."""
    listing: dict[int, list[str]] = {}
    for circuit, held in spaces.items():
        for space in held:
            listing.setdefault(space, []).append(circuit)
    return [
        (space, listed)
        for space, listed in sorted(listing.items())
        if len(listed) > MAX_CIRCUITS_PER_SPACE
    ]


def crowding(space: int, listed: list[str]) -> str:
    """Why the circuits *listed* cannot all list *space*, to follow their names."""
    return (
        f"{listed} all list space {space}, but a breaker space holds at most two circuits: "
        "a tandem's two single-pole circuits, a quad's two two-pole ones, or a tandem-quad's "
        "single-pole one and its two-pole one"
    )


def breaker_misfit(spaces: Mapping[str, frozenset[int]]) -> str | None:
    """Why the circuits *spaces* holds, by id, cannot be one breaker's, or None if they can."""
    held = sorted(frozenset[int]().union(*spaces.values()))
    spanned = any(circuit_spaces == frozenset(held) for circuit_spaces in spaces.values())
    if len(held) > 2 or (len(held) == 2 and not (held[1] - held[0] == 2 and spanned)):
        return (
            f"they list spaces {held}: a multi-load breaker sits on one space, or one "
            "two-pole pair that one of its circuits spans"
        )
    crowded = crowded_spaces(spaces)
    if crowded:
        space, listed = crowded[0]
        return crowding(space, listed)
    return None
