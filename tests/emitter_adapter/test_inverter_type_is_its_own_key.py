"""`priority` means shedding. Whether an inverter is grid-forming is a separate key.

The two used to be one field. `priority: MUST_HAVE` meant "shed this circuit last"
*and* "this inverter is grid-forming", so the dashboard's Inverter Type control read
and wrote the circuit's shed priority: choosing *Hybrid* silently rewrote the
published `default-priority`, and a site that sheds its producer last while running a
grid-following inverter could not be expressed at all.

Splitting them exposed a second bug that the overload had been hiding. `_islandable`
consulted `profile["pv"]["inverter_type"]` -- a top-level `pv:` section that no
simulator config has -- while the engine read the producer template. The two agreed
only because every config left the producer non-hybrid, so both said "not islandable"
for different reasons. The moment a config said otherwise they disagreed, and the
panel would island in simulation while advertising no islanding authority: no MID, so
a consumer falls back to inferring grid state from power flow, which is the heuristic
v1.0 exists to retire.

That second bug is now fixed at the root rather than by making the two readers agree:
islandability follows the commissioned battery, and the inverter does not decide it at
all. What survives here is the first bug -- `priority` means shedding and nothing else
-- so these tests measure the published `inverter-type` where they used to measure the
MID, which no longer varies with the inverter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from panelbench.emitter_adapter.spec_generator import build_manifest

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig


def _profile(*, inverter_type: str | None, priority: str = "NEVER") -> SimulationConfig:
    """An islandable-capable site whose islandability comes only from its template.

    No `panel_config.islandable` on purpose: the override would satisfy the gate
    regardless and mask the path under test.
    """
    template: dict[str, Any] = {
        "energy_profile": {"mode": "producer", "typical_power": -6000.0},
        "priority": priority,
        "device_type": "pv",
    }
    if inverter_type is not None:
        template["inverter_type"] = inverter_type
    return cast(
        "SimulationConfig",
        {
            "panel_config": {
                "serial_number": "sim-test-001",
                "total_tabs": 40,
                "main_size": 200,
            },
            "bess": {"enabled": True, "vendor": "Span", "nameplate_capacity_kwh": 13.5},
            "circuit_templates": {"solar": template},
            "circuits": [{"id": "solar_1", "name": "Solar", "template": "solar", "tabs": [30]}],
        },
    )


def _mid_count(profile: SimulationConfig) -> int:
    return sum(1 for inst in build_manifest(profile).instances if inst.entity_class == "mid")


def _published_inverter_type(profile: SimulationConfig) -> str | None:
    for inst in build_manifest(profile).instances:
        if inst.entity_class == "pv":
            return inst.metadata.get("inverter-type")
    return None


@pytest.mark.parametrize("inverter_type", ["hybrid", "ac-coupled"])
def test_the_producer_template_no_longer_decides_islandability(inverter_type: str) -> None:
    """Reversed deliberately: the inverter stopped being the gate.

    This used to assert 1 for hybrid and 0 for ac-coupled, reading islandability off
    the producer template. Both sites here have a commissioned battery, and a battery
    is what forms a premises island -- so both publish a MID, and the inverter beside
    it does not get a vote. The ac-coupled case is the one that was wrong: a
    grid-forming battery next to AC-coupled solar is an ordinary installation that was
    silently denied its islanding authority.

    Still asserting the MID rather than a boolean, for the original reason: the MID is
    the consumer-visible consequence, carrying `islanding-state` and
    `grid-forming-entity`, so its absence is what a parser actually notices.
    """
    assert _mid_count(_profile(inverter_type=inverter_type)) == 1


def test_shed_priority_no_longer_decides_the_inverter() -> None:
    """The de-overloading, pinned where it would regress.

    This combination -- sheds last, grid-following -- was inexpressible while the two
    keys were one, and it is the one that flips if anything starts reading `priority`
    for the inverter type again.

    Measured on the published `inverter-type` rather than on the MID. The MID no
    longer discriminates here -- a commissioned battery has one whatever the inverter
    is -- so asserting its absence would have quietly stopped testing the overload and
    started testing the gate. `inverter-type` is where `priority` would actually leak.
    """
    profile = _profile(inverter_type="ac-coupled", priority="MUST_HAVE")

    assert _published_inverter_type(profile) == "ac-coupled", (
        "MUST_HAVE is a shedding answer, not a grid-forming one; an explicit "
        "ac-coupled inverter must win"
    )


def test_a_clone_written_before_the_split_still_reads_as_hybrid() -> None:
    """Backward compatibility, kept honest.

    Configs written before `inverter_type` existed encoded hybrid as
    `priority: MUST_HAVE`, and such clones are on disk. The fallback reads that
    encoding but never writes it, so an old config keeps working and a new one is
    unambiguous.

    Asserted on the published type for the same reason as the test above: this profile
    has a battery, so its MID count is 1 either way and would no longer notice the
    fallback breaking.
    """
    assert _published_inverter_type(_profile(inverter_type=None, priority="MUST_HAVE")) == "hybrid"


def test_the_published_inverter_type_follows_the_same_resolver() -> None:
    """What the PV advertises and what gates the MID cannot disagree.

    `inverter-type` used to be resolved from the phantom `pv:` section, so it read
    `ac-coupled` for every config while the MID gate read the template. A site could
    therefore publish a grid-following inverter and an islanding authority at once.
    """
    assert _published_inverter_type(_profile(inverter_type="hybrid")) == "hybrid"
    assert _published_inverter_type(_profile(inverter_type="ac-coupled")) == "ac-coupled"
