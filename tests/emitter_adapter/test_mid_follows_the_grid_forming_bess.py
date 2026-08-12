"""The MID belongs to the battery, not to the solar inverter.

`devices/bess.md` classifies backup topology on the capability set rather than on any
declared type: "a MID `grid` child means premises-segment backup ... neither means no
backup", and "a premises-wiring grid-forming BESS publisher MUST include a MID child
device". The MID's presence *is* the signal, which is also why v1.0 has no
`grid-islandable` boolean -- "there is no single 'islanded?' bit to reconcile".

This gate read the PV inverter instead. The two agree for a hybrid-inverter site,
which is why a MID appeared at all, and they diverge for the cases the spec cares
most about: a grid-forming battery with AC-coupled PV, and a grid-forming battery
with no PV at all -- the canonical residential backup product, which published a
battery and no MID, leaving the spec's MUST unmet and a consumer with nothing to read
islanding state from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from panelbench.emitter_adapter.spec_generator import build_manifest

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig

_BESS = {"enabled": True, "vendor": "Span", "nameplate_capacity_kwh": 13.5}


def _profile(*, bess: dict[str, Any] | None = None, **top: Any) -> SimulationConfig:
    profile: dict[str, Any] = {
        "panel_config": {"serial_number": "sim-40t-001", "total_tabs": 40, "main_size": 200},
        "circuits": [],
        "bess": {**_BESS, **(bess or {})},
    }
    profile.update(top)
    return cast("SimulationConfig", profile)


def _has_mid(profile: SimulationConfig) -> bool:
    return any(inst.entity_class == "mid" for inst in build_manifest(profile).instances)


def test_a_grid_forming_battery_with_no_pv_publishes_a_mid() -> None:
    """The canonical residential backup product, and the case that was broken.

    A Powerwall-style panel is a battery and a transfer switch with no solar at all.
    Reading the PV inverter gave it no MID, so the one device carrying islanding state
    was missing from exactly the product the MID exists to describe.
    """
    assert _has_mid(_profile(bess={"grid_forming": True}))


def test_a_grid_forming_battery_beside_ac_coupled_pv_publishes_a_mid() -> None:
    """The other divergence.

    A hybrid inverter implies a grid-forming battery, but the converse does not hold:
    a grid-forming battery can sit beside AC-coupled solar. Inferring from the
    inverter gets this backwards and omits the required MID.
    """
    profile = _profile(
        bess={"grid_forming": True},
        pv={"enabled": True, "vendor": "Enphase", "inverter_type": "ac-coupled"},
    )

    assert _has_mid(profile)


def test_a_grid_following_battery_publishes_no_mid() -> None:
    """Absence is a signal too.

    `bess.md` lists "grid-following BESS with no backup ... neither a MID child nor
    `output-island`", and a consumer reads that absence as "no premises-wiring
    backup". Emitting a MID anyway would assert backup this site does not have.
    """
    assert not _has_mid(_profile(bess={"grid_forming": False}))


def test_an_undeclared_battery_is_not_assumed_to_form_an_island() -> None:
    """Defaulting to grid-forming would fabricate a capability.

    Not every battery backs the premises up. With nothing declared and no legacy
    signal, the honest answer is the one that claims less -- a config that wants a MID
    says so.
    """
    assert not _has_mid(_profile())


@pytest.mark.parametrize(
    ("description", "profile_kwargs"),
    [
        (
            "panel_config.islandable, the flat schema's retired boolean",
            {
                "panel_config": {
                    "serial_number": "sim-40t-001",
                    "total_tabs": 40,
                    "main_size": 200,
                    "islandable": True,
                }
            },
        ),
        (
            "a hybrid PV inverter, the pre-BESS inference",
            {"pv": {"enabled": True, "vendor": "Enphase", "inverter_type": "hybrid"}},
        ),
    ],
)
def test_clones_written_before_the_key_still_publish_a_mid(
    description: str, profile_kwargs: dict[str, Any]
) -> None:
    """Both legacy signals keep working, because clones on disk carry them.

    Read but never written: a new config states `grid_forming` outright. Dropping
    either would silently remove the MID from a config that has one today, which
    reaches a user as islanding state disappearing after an upgrade.
    """
    assert _has_mid(_profile(**profile_kwargs)), description


def test_the_explicit_key_overrides_a_legacy_signal() -> None:
    """Most explicit wins, in both directions.

    A config that inherited `islandable: true` and has since learned its battery is
    grid-following must be able to say so without editing the legacy key it may not
    own.
    """
    profile = _profile(
        bess={"grid_forming": False},
        panel_config={
            "serial_number": "sim-40t-001",
            "total_tabs": 40,
            "main_size": 200,
            "islandable": True,
        },
    )

    assert not _has_mid(profile)
