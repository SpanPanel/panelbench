"""The hardware version a PanelBench panel reports.

One value per panel, the config's ``hardware_version``, else ``1.2``, the value a
captured SPAN Panel publishes (tests/fidelity/fixtures/upstream). The panel publishes
it as ``info/hardware-version``, a free string in SPAN's MQTT topic reference. From SPAN
release 202639, ``GET /api/v2/status`` also reports the panel's hardware version as
the required ``hardwareVersion``, which SPAN documents as ``1.2`` or ``2.0``, and
``UNKNOWN`` when the panel cannot determine it (SPAN-API-Client-Docs, Release 202639).
The status therefore reads the same value through that enumeration: a documented
value is reported as is, and anything else, such as ``rev2``, as ``UNKNOWN``. One key,
read two ways, rather than a second key that could disagree with the first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from panelbench.firmware import SPAN_RELEASE_202639, panel_firmware_version, predates

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ebus_panel_sim import Variant

    from panelbench.config_types import SimulationConfig

DEFAULT_HARDWARE_VERSION: Final = "1.2"

_EXTENDED_VARIANT: Final = "span-alpha-test-b2"
"""The emitter variant that adds site, busbar and frequency properties, commissioning
facts and a meter outside the panel to the ``span`` one."""

_VARIANT_BY_HARDWARE_VERSION: Final[Mapping[str, Variant]] = {"3.0": _EXTENDED_VARIANT}
"""The emitter variant a hardware version string selects, as the emitter's capture
reads a SPAN panel; every other string publishes ``span``."""

_STATUS_HARDWARE_VERSIONS: Final = frozenset({"1.2", "2.0"})
_STATUS_HARDWARE_UNKNOWN: Final = "UNKNOWN"


def panel_hardware_version(config: Mapping[str, object]) -> str:
    """The hardware version the panel *config* describes, as MQTT publishes it.

    ``str()`` because YAML reads an unquoted ``1.2`` or ``2.0`` as a float. Takes any
    mapping, not only a ``SimulationConfig``, because validation asks it of a config
    that is still unvalidated YAML.
    """
    return str(config.get("hardware_version") or DEFAULT_HARDWARE_VERSION)


def variant_for(hardware_version: str) -> Variant:
    """The set of device profiles a panel reporting *hardware_version* publishes.

    Selected by the string, as a panel's own hardware decides what it declares, so a
    clone that keeps the string publishes what its panel does.
    """
    return _VARIANT_BY_HARDWARE_VERSION.get(hardware_version, "span")


def panel_variant(config: Mapping[str, object]) -> Variant:
    """``variant_for`` the panel *config* describes."""
    return variant_for(panel_hardware_version(config))


def publishes_outside_meters(config: Mapping[str, object]) -> bool:
    """Whether the panel *config* describes can publish a meter outside the panel."""
    return panel_variant(config) == _EXTENDED_VARIANT


def publishes_pv_devices(variant: Variant) -> bool:
    """Whether *variant* publishes an inverter as a device of its own.

    The variant that publishes meters outside the panel does not: the circuit feeding
    an inverter carries the solar feeds role instead.
    """
    return variant != _EXTENDED_VARIANT


def relay_locks_priority(variant: Variant) -> bool:
    """Whether a circuit's locked relay also locks its shed priority under *variant*.

    So it is under the variant with no commissioned-system circuits: a PV or battery
    breaker there is an ordinary circuit with a locked relay at priority NEVER.
    """
    return variant == _EXTENDED_VARIANT


def status_hardware_version(config: SimulationConfig) -> str | None:
    """``hardwareVersion`` for ``GET /api/v2/status``, or None before release 202639.

    A release before 202639 did not report the field, so a panel naming one leaves
    it out; a panel naming 202639 or later, or no release, reports it.
    """
    if predates(panel_firmware_version(config), SPAN_RELEASE_202639):
        return None
    version = panel_hardware_version(config)
    return version if version in _STATUS_HARDWARE_VERSIONS else _STATUS_HARDWARE_UNKNOWN
