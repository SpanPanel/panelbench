"""A PV circuit on one tab is warned about, never refused.

A grid-tied inverter in a US panel is 240 V on a two-pole breaker: two tabs on
opposite legs, as every shipped template's PV circuit is. One on a single tab is
almost always a modelling slip, such as a load circuit turned into an inverter
without its second pole, but a 120 V PV circuit is unusual rather than impossible,
and existing configs must keep loading. So validation names the circuit in a
WARNING and carries on.
"""

from __future__ import annotations

import logging

import pytest

from panelbench.config_types import SimulationConfig
from panelbench.validation import validate_yaml_config
from tests._helpers import default_config


def _with_solar_tabs(tabs: list[int], *, commissioned: bool = False) -> SimulationConfig:
    """The default panel, its solar circuit on *tabs*."""
    config = default_config()
    [solar] = [c for c in config["circuits"] if c["id"] == "solar_inverter"]
    solar["tabs"] = tabs
    if commissioned:
        config["firmware_version"] = "spanos3/r202639/03"
        template = config["circuit_templates"][solar["template"]]
        template["commissioned_system"] = "pv"
        template["relay_behavior"] = "non-controllable"
        template["priority"] = "NEVER"
    return config


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("commissioned", [False, True], ids=["pv", "commissioned pv"])
def test_a_pv_circuit_on_one_tab_loads_with_a_warning(
    caplog: pytest.LogCaptureFixture, *, commissioned: bool
) -> None:
    with caplog.at_level(logging.WARNING):
        validate_yaml_config(_with_solar_tabs([36], commissioned=commissioned))

    assert any(
        "'solar_inverter'" in message and "two-pole" in message for message in _warnings(caplog)
    ), _warnings(caplog)


@pytest.mark.parametrize("tabs", [[36, 38], []], ids=["two-pole", "virtual"])
def test_a_two_pole_or_virtual_pv_circuit_is_not_warned_about(
    caplog: pytest.LogCaptureFixture, tabs: list[int]
) -> None:
    """A PV circuit with no tabs is a what-if one, which occupies no breaker."""
    with caplog.at_level(logging.WARNING):
        validate_yaml_config(_with_solar_tabs(tabs))

    assert not any("solar_inverter" in message for message in _warnings(caplog))
