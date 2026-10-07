"""A circuit's rating has one source: its template's ``energy_profile.nameplate_capacity_w``.

That is the key the dashboard edits and every reader reads: the engine scales a
producer by it, the manifest publishes it as ``nominal-power-w``, and modelling and
the history generator size their estimates by it. Three older places can also hold
a rating, and each used to win somewhere different, so a panel could publish one
rating and produce at another:

- the template's top level, where configs written for the earlier emitter fork put
  it;
- a circuit's ``overrides``, which the engine applied to its own circuit and nothing
  else read;
- the ``pv`` section, which rates the inverter of the circuit ``pv.feed`` or the
  sole-circuit rule binds (``pv_section.bound_pv_circuit_id``).

``fold_legacy_ratings`` moves each into the profile and ``rating_conflicts`` names
what cannot be moved, both from one plan, so the loader and validation cannot
disagree about which is which. A legacy value equal to the profile's, or one where
the profile states none, folds silently.

Where a stated profile and a legacy value disagree, which one is the user's depends
on who could have written it. Every released dashboard wrote a nameplate edit to the
profile alone, and no released writer ever wrote the template's top level or a
circuit's override; the shipped MAIN 40 and MAIN 32 templates carried one of those
two, and a template clone copied it. So against either of those the profile wins,
the stale copy is dropped, and a WARNING names both values and the file. Only a
hand edit puts a rating in the ``pv`` section, or two legacy ratings where the
profile states none, so a disagreement there is refused, naming both keys and both
values, because picking either would be a guess. The ``pv`` section's rating stays
where it is only for an inverter with no PV circuit, one upstream of the panel,
where it is the sole source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from panelbench.pv_section import bound_pv_circuit_id, pv_circuit_ids

if TYPE_CHECKING:
    from collections.abc import Mapping

NAMEPLATE = "nameplate_capacity_w"

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Claim:
    """One legacy place a template's rating is written."""

    where: str
    value: object
    holder: dict[str, object]
    # The circuit a circuit-level claim rates; None for the template's own top level,
    # which rates every circuit built on it.
    circuit_id: str | None
    # A mapping left empty once the claim is removed, and where it hangs, so a folded
    # override does not leave `overrides: {}` behind.
    empties: tuple[dict[str, object], str] | None = None
    # Whether a released writer could have left this behind beside a dashboard edit:
    # true of the template's top level and a circuit's override, never of the `pv`
    # section, which only a hand edit fills.
    stale_beside_an_edit: bool = True


@dataclass(frozen=True)
class _Fold:
    """Claims removed from one template's circuits, and the rating they leave behind."""

    profile: dict[str, object]
    rating: float | None
    claims: tuple[_Claim, ...]
    # Why a stale copy was dropped rather than folded, for the WARNING each gets.
    dropped: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Plan:
    folds: tuple[_Fold, ...]
    conflicts: tuple[str, ...]


def fold_legacy_ratings(
    config: Mapping[str, object], *, unstated: frozenset[str], source: str
) -> None:
    """Move every legacy rating in *config* into its template's profile, in place.

    *unstated* names the templates whose profile the loader filled in from a default:
    the default's rating is not one the config stated, so a legacy rating replaces it.
    A stale copy beside a stated profile is dropped, with a WARNING naming *source*,
    the file or config it came from. What else disagrees is left where it is, for
    ``rating_conflicts`` to refuse.
    """
    for fold in _plan(config, unstated=unstated).folds:
        for reason in fold.dropped:
            _LOGGER.warning("%s: %s", source, reason)
        if fold.rating is not None:
            fold.profile[NAMEPLATE] = fold.rating
        for claim in fold.claims:
            claim.holder.pop(NAMEPLATE, None)
            if claim.empties is not None and not claim.holder:
                parent, key = claim.empties
                parent.pop(key, None)


def rating_conflicts(config: Mapping[str, object]) -> tuple[str, ...]:
    """Why each legacy rating in *config* that cannot be folded into its profile cannot."""
    return _plan(config, unstated=frozenset()).conflicts


def _plan(config: Mapping[str, object], *, unstated: frozenset[str]) -> _Plan:
    templates = config.get("circuit_templates")
    if not isinstance(templates, dict):
        return _Plan((), ())
    circuits = config.get("circuits")
    if not isinstance(circuits, list):
        circuits = []
    users: dict[str, list[dict[str, object]]] = {}
    for circuit in circuits:
        if isinstance(circuit, dict) and isinstance(circuit.get("template"), str):
            users.setdefault(circuit["template"], []).append(circuit)

    pv_cfg = config.get("pv")
    section = pv_cfg if isinstance(pv_cfg, dict) and NAMEPLATE in pv_cfg else None
    folds: list[_Fold] = []
    conflicts: list[str] = []
    bound = _section_circuit_id(config, section, conflicts)

    for name, template in templates.items():
        if not isinstance(template, dict) or not isinstance(template.get("energy_profile"), dict):
            continue
        claims = _claims(str(name), template, users.get(name, []), section, bound)
        if not claims:
            continue
        stated = None if str(name) in unstated else template["energy_profile"].get(NAMEPLATE)
        fold, refused = _template_plan(str(name), template, users.get(name, []), claims, stated)
        if fold is not None:
            folds.append(fold)
        conflicts.extend(refused)
    return _Plan(tuple(folds), tuple(conflicts))


def _section_circuit_id(
    config: Mapping[str, object], section: dict[str, object] | None, conflicts: list[str]
) -> str | None:
    """The circuit the ``pv`` section's rating rates, or None when it rates no circuit.

    A section rating with several PV circuits and none bound rates nothing, which is
    a conflict, not something to drop. With no PV circuit at all the section's rating
    is the sole source and stays put. A binding the section cannot make is refused by
    validation in its own words, so it is not reported twice here.
    """
    if section is None:
        return None
    try:
        bound = bound_pv_circuit_id(config)
    except ValueError:
        return None
    pv_ids = pv_circuit_ids(config)
    if bound is None and len(pv_ids) > 1:
        ids = ", ".join(repr(circuit_id) for circuit_id in pv_ids)
        conflicts.append(
            f"pv.nameplate_capacity_w is {_shown(section[NAMEPLATE])}, but the pv section "
            f"describes none of PV circuits {ids}: set pv.feed to the circuit it rates, or "
            "move the rating to that circuit template's energy_profile.nameplate_capacity_w"
        )
    return bound


def _claims(
    name: str,
    template: dict[str, object],
    circuits: list[dict[str, object]],
    section: dict[str, object] | None,
    bound: str | None,
) -> list[_Claim]:
    claims: list[_Claim] = []
    if NAMEPLATE in template:
        claims.append(
            _Claim(f"circuit_templates.{name}.{NAMEPLATE}", template[NAMEPLATE], template, None)
        )
    for circuit in circuits:
        overrides = circuit.get("overrides")
        if isinstance(overrides, dict) and NAMEPLATE in overrides:
            circuit_id = str(circuit.get("id"))
            claims.append(
                _Claim(
                    f"circuits[{circuit_id}].overrides.{NAMEPLATE}",
                    overrides[NAMEPLATE],
                    overrides,
                    circuit_id,
                    empties=(circuit, "overrides"),
                )
            )
    rates_one_of_these = any(str(circuit.get("id")) == bound for circuit in circuits)
    if section is not None and bound is not None and rates_one_of_these:
        claims.append(
            _Claim(
                f"pv.{NAMEPLATE}",
                section[NAMEPLATE],
                section,
                bound,
                stale_beside_an_edit=False,
            )
        )
    return claims


def _template_plan(
    name: str,
    template: dict[str, object],
    circuits: list[dict[str, object]],
    claims: list[_Claim],
    stated: object,
) -> tuple[_Fold | None, tuple[str, ...]]:
    """What can fold into *template*'s profile, and why the rest cannot.

    *stated* is the profile's own rating, None where it states none.
    """
    profile = template["energy_profile"]
    assert isinstance(profile, dict)
    canonical_key = f"circuit_templates.{name}.energy_profile.{NAMEPLATE}"
    not_numbers = tuple(
        f"{claim.where} is {claim.value!r}, which is not a rating in watts"
        for claim in claims
        if _watts(claim.value) is None
    )
    if not_numbers:
        return None, not_numbers

    if stated is not None:
        canonical = _watts(stated)
        disagreeing = [claim for claim in claims if _watts(claim.value) != canonical]
        stale = [claim for claim in disagreeing if claim.stale_beside_an_edit]
        dropped = tuple(
            f"{claim.where} is {_shown(claim.value)} but {canonical_key}, which the "
            f"dashboard edits, is {_shown(stated)}: rating the circuit {_shown(stated)} "
            f"and dropping the stale {claim.where}"
            for claim in stale
        )
        removed = tuple(
            claim
            for claim in claims
            if _watts(claim.value) == canonical or claim.stale_beside_an_edit
        )
        refused = tuple(
            _disagreement(name, claim, canonical_key, stated, circuits)
            for claim in disagreeing
            if not claim.stale_beside_an_edit
        )
        return _Fold(profile, None, removed, dropped), refused

    ratings = {_watts(claim.value) for claim in claims}
    if len(ratings) > 1:
        stated_by = ", ".join(f"{claim.where} is {_shown(claim.value)}" for claim in claims)
        return None, (
            f"{stated_by}, and {canonical_key} states none: set {canonical_key} and remove "
            "the others",
        )

    rated = {claim.circuit_id for claim in claims}
    if None not in rated:
        unrated = [
            str(circuit.get("id")) for circuit in circuits if str(circuit.get("id")) not in rated
        ]
        if unrated:
            shared = ", ".join(repr(circuit_id) for circuit_id in unrated)
            return None, (
                f"{claims[0].where} is {_shown(claims[0].value)}, but circuit template "
                f"{name!r} is shared with {shared}, which it does not rate: give the rated "
                f"circuit its own template and set its energy_profile.{NAMEPLATE}",
            )
    [rating] = ratings
    return _Fold(profile, rating, tuple(claims)), ()


def _disagreement(
    name: str,
    claim: _Claim,
    canonical_key: str,
    stated: object,
    circuits: list[dict[str, object]],
) -> str:
    """Why *claim* is refused beside a profile that states *stated*.

    Making the two agree re-rates every circuit on the template, so where it rates one
    circuit of several the advice is the template of its own instead.
    """
    disagree = f"{claim.where} is {_shown(claim.value)} but {canonical_key} is {_shown(stated)}"
    others = [
        str(circuit.get("id"))
        for circuit in circuits
        if str(circuit.get("id")) != claim.circuit_id
    ]
    if claim.circuit_id is not None and others:
        shared = ", ".join(repr(circuit_id) for circuit_id in others)
        return (
            f"{disagree}, and circuit template {name!r} is shared with {shared}: give the "
            f"rated circuit its own template, set its energy_profile.{NAMEPLATE} and remove "
            f"{claim.where}"
        )
    return f"{disagree}: a rating has one source, so remove {claim.where} or make the two agree"


def _watts(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _shown(value: object) -> str:
    watts = _watts(value)
    return repr(value) if watts is None else str(watts)
