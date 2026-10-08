"""A solar circuit left unlocked on release 202639 is warned about, never refused.

From SPAN release 202639 every commissioned inverter is its own PV device, named
by the circuit feeding it, and the circuits the panel adds for a commissioned PV
system are locked: relay not switchable, priority permanently NEVER
(SPAN-API-Client-Docs CHANGELOG, Release 202639). A circuit names a device it feeds
only when that device is commissioned, so on that release a circuit feeding an
inverter is a locked one. A config naming it whose PV circuit has no
`commissioned_system: pv` publishes a circuit the release never does.

Warned, not refused: a config that loaded before must keep loading, such as a
template shipped before the key existed, and a clone of a panel recognises a
commissioned circuit by the name SPAN gives it, so it can produce one.
"""

from __future__ import annotations

import logging

import pytest

from panelbench.config_types import SimulationConfig
from panelbench.validation import validate_yaml_config
from tests._helpers import CURRENT_FIRMWARE, EARLIER_FIRMWARE, default_config


def _solar_on(
    firmware: str, *, commissioned: bool, tabs: list[int] | None = None
) -> SimulationConfig:
    """The default panel naming *firmware*, its solar circuit locked or not."""
    config = default_config()
    config["firmware_version"] = firmware
    [solar] = [c for c in config["circuits"] if c["id"] == "solar_inverter"]
    if tabs is not None:
        solar["tabs"] = tabs
    template = config["circuit_templates"][solar["template"]]
    template["relay_behavior"] = "non-controllable"
    template["priority"] = "NEVER"
    if commissioned:
        template["commissioned_system"] = "pv"
    else:
        template.pop("commissioned_system", None)
    return config


def _unlocked_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "commissioned_system: pv" in r.getMessage()
    ]


@pytest.mark.parametrize(
    "firmware",
    # A string naming no release is taken to be current, as the emitter takes it.
    [CURRENT_FIRMWARE, "sim/v0.1.0"],
)
def test_an_uncommissioned_pv_circuit_loads_with_a_warning(
    caplog: pytest.LogCaptureFixture, firmware: str
) -> None:
    with caplog.at_level(logging.WARNING):
        validate_yaml_config(_solar_on(firmware, commissioned=False))

    [message] = _unlocked_warnings(caplog)
    assert "'solar_inverter'" in message
    assert "'solar'" in message


def test_a_commissioned_pv_circuit_is_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        validate_yaml_config(_solar_on(CURRENT_FIRMWARE, commissioned=True))

    assert not _unlocked_warnings(caplog)


def test_a_pv_circuit_before_release_202639_is_not_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Before release 202639 the circuit is unlocked, and the key is refused there."""
    with caplog.at_level(logging.WARNING):
        validate_yaml_config(_solar_on(EARLIER_FIRMWARE, commissioned=False))

    assert not _unlocked_warnings(caplog)


def test_a_virtual_pv_circuit_is_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    """A PV circuit with no tabs is a what-if one, which no breaker feeds."""
    with caplog.at_level(logging.WARNING):
        validate_yaml_config(_solar_on(CURRENT_FIRMWARE, commissioned=False, tabs=[]))

    assert not _unlocked_warnings(caplog)
