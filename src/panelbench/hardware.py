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
    from panelbench.config_types import SimulationConfig

DEFAULT_HARDWARE_VERSION: Final = "1.2"

_STATUS_HARDWARE_VERSIONS: Final = frozenset({"1.2", "2.0"})
_STATUS_HARDWARE_UNKNOWN: Final = "UNKNOWN"


def panel_hardware_version(config: SimulationConfig) -> str:
    """The hardware version the panel *config* describes, as MQTT publishes it.

    ``str()`` because YAML reads an unquoted ``1.2`` or ``2.0`` as a float.
    """
    return str(config.get("hardware_version") or DEFAULT_HARDWARE_VERSION)


def status_hardware_version(config: SimulationConfig) -> str | None:
    """``hardwareVersion`` for ``GET /api/v2/status``, or None before release 202639.

    A release before 202639 did not report the field, so a panel naming one leaves
    it out; a panel naming 202639 or later, or no release, reports it.
    """
    if predates(panel_firmware_version(config), SPAN_RELEASE_202639):
        return None
    version = panel_hardware_version(config)
    return version if version in _STATUS_HARDWARE_VERSIONS else _STATUS_HARDWARE_UNKNOWN
