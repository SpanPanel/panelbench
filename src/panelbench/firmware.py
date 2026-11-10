"""The firmware string a PanelBench panel reports, and the SPAN release it names.

One string per panel, from the config, else the package's own version. The panel
publishes it as ``info/firmware-version``, reports it from its HTTP status endpoint
and advertises it over mDNS, because a consumer reading any two of those must see
one panel.

The emitter decides the BESS meter's sign and the EVSE user limit from the string
itself. PanelBench needs the same release for what the emitter does not derive,
the per-inverter PV devices and the REST status, so it reads it here by the same
rule: SPAN firmware strings are ``/``-separated, and the segment ``r`` plus six
digits names the release as year and week (``spanos2/r202639/03`` is release
202639, as SPAN-API-Client-Docs' CHANGELOG numbers them). A string naming no
release, such as ``sim/v0.1.0``, is taken to be current, as the emitter takes it.
``tests/emitter_adapter/test_firmware_conventions.py`` fails if the two rules ever
disagree on the wire.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from panelbench.const import DEFAULT_FIRMWARE_VERSION

if TYPE_CHECKING:
    from collections.abc import Mapping

SPAN_RELEASE_202639: Final = 202639
"""The SPAN release that reversed the BESS meter's sign, left the EVSE user limit
unpublished until set, published one PV device per inverter, and added
``hardwareVersion`` to ``GET /api/v2/status`` (SPAN-API-Client-Docs, Release 202639).
Its panels also name each circuit's device after its id, keeping the circuit's own
name in ``info/name``, as the emitter's captured MAIN 32 on the release shows.
"""

_RELEASE_SEGMENT: Final = re.compile(r"r(\d{6})")


def panel_firmware_version(config: Mapping[str, object]) -> str:
    """The firmware string the panel *config* describes reports everywhere.

    ``str()`` because YAML reads an unquoted all-digit value as a number. Takes any
    mapping, not only a ``SimulationConfig``, because validation asks it of a config
    that is still unvalidated YAML.
    """
    return str(config.get("firmware_version") or DEFAULT_FIRMWARE_VERSION)


def release_build(firmware: str) -> int | None:
    """The SPAN release a firmware string names, or None when it names none.

    Only a whole ``/``-separated segment of ``r`` plus exactly six digits counts, so
    ``r2026390`` names nothing; the first such segment wins.
    """
    for segment in firmware.strip().split("/"):
        match = _RELEASE_SEGMENT.fullmatch(segment)
        if match is not None:
            return int(match.group(1))
    return None


def predates(firmware: str, release: int) -> bool:
    """Whether *firmware* names a SPAN release before *release*.

    A string naming no release predates nothing, so a simulator string gets the
    current behaviour, as the emitter gives it.
    """
    build = release_build(firmware)
    return build is not None and build < release
