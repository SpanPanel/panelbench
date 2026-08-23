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

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
import yaml

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


def test_no_battery_means_no_mid() -> None:
    """The other half of the invariant, and the limit on the new default.

    A MID belongs to a BESS -- it is the battery's islanding authority, published as
    its child. Defaulting an *undeclared* battery to grid-forming widens which
    batteries get one; it must not conjure one where there is no battery to parent it
    to. Checked in both shapes a config can take: no `bess` key at all, and one that
    is present but disabled.
    """
    no_key = cast(
        "SimulationConfig",
        {
            "panel_config": {"serial_number": "sim-40t-001", "total_tabs": 40, "main_size": 200},
            "circuits": [],
        },
    )
    assert not _has_mid(no_key)
    assert not _has_mid(_profile(bess={"enabled": False}))


def test_a_grid_following_battery_publishes_no_mid() -> None:
    """Absence is a signal too.

    `bess.md` lists "grid-following BESS with no backup ... neither a MID child nor
    `output-island`", and a consumer reads that absence as "no premises-wiring
    backup". Emitting a MID anyway would assert backup this site does not have.
    """
    assert not _has_mid(_profile(bess={"grid_forming": False}))


def test_an_undeclared_battery_publishes_a_mid() -> None:
    """A battery implies a MID. Reversed deliberately; the old default was wrong here.

    The earlier rule claimed less on purpose: with nothing declared, assume nothing.
    That reads well and fails in practice, because the configs that declare nothing
    are the overwhelming majority -- everything the flat simulator ever wrote, every
    clone taken before `grid_forming` existed, and two of this repository's own
    shipped defaults. All of them have a battery and none of them said so, so the
    honest-looking default silently produced the one thing a consumer cannot work
    around: no MID, and therefore nothing publishing islanding state, with no error
    to explain it.

    Claiming less is only honest when the claim is actually uncertain. A commissioned
    BESS in a SPAN enclosure forms a premises island; that is what the product is. A
    site where it does not still says so, with `grid_forming: false`, which is a
    statement someone makes knowingly rather than one thousands of existing configs
    make by omission.
    """
    assert _has_mid(_profile())


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


def test_every_shipped_config_with_a_battery_publishes_a_mid() -> None:
    """The invariant, checked against the files we actually ship.

    `default_MAIN_16` and `default_MAIN_32` both enabled a battery and published no
    MID, so a fresh install of either -- no stale config anywhere, nothing upgraded --
    showed a battery with no islanding state. The unit tests above all passed while
    that was true, because every one of them builds its own profile. This one reads
    the configs off disk, which is the only way that gap was ever going to be caught.
    """
    config_dir = Path(__file__).resolve().parents[2] / "configs"
    shipped = sorted(config_dir.glob("*.yaml"))
    assert shipped, f"no configs found under {config_dir}"

    missing = []
    for path in shipped:
        profile = cast("SimulationConfig", yaml.safe_load(path.read_text()))
        bess = profile.get("bess") or {}
        if not bess.get("enabled"):
            continue
        if not _has_mid(profile):
            missing.append(path.name)

    assert not missing, f"configs enable a BESS but publish no MID: {missing}"
