"""Absolute checks: a capture measured against the published contract.

`comparator.py` is differential — it asks whether two producers agree. That
question is blind wherever they agree *and are both wrong*, which is the common
case for anything inherited from the same example code. These checks compare a
single capture against what SPAN firmware documents, so agreement between the
two producers buys nothing.
"""

from __future__ import annotations

import re

from .comparator import class_of, declared_properties, role_of

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def declared_but_unvalued(devices: dict[str, dict[str, str]]) -> set[str]:
    """``<role>  <node>/<property>`` for every declaration carrying no retained value.

    A consumer builds entities from ``$description``, so a property declared and
    never published is an entity that never receives a state.
    """
    unvalued: set[str] = set()
    for device_id, body in devices.items():
        role = role_of(device_id, body)
        published = {key for key in body if not key.startswith("$")}
        unvalued.update(f"{role}  {path}" for path in declared_properties(body) - published)
    return unvalued


def panel_serial(devices: dict[str, dict[str, str]]) -> str | None:
    """The panel's own serial, read from its ``info`` capability.

    Taken from the published property rather than from the panel's device id,
    because the id is the thing under test — deriving the expectation from it
    would make the panel's check vacuously true.
    """
    for body in devices.values():
        if class_of(body) == "distribution-enclosure":
            return body.get("info/serial-number")
    return None


def _expected_form(
    device_class: str, device_id: str, serial: str, bess_ids: set[str]
) -> str | None:
    """The documented pattern *device_id* fails, or None when it conforms.

    Patterns are the migration guide's Device ID Stability table. Note its
    warning that vendor serials may contain hyphens: classification is by
    declared type and the checks are prefix or equality tests, never a split
    on ``-`` to recover components.
    """
    if device_class == "distribution-enclosure":
        return None if device_id == serial else "<panel-serial>"
    if device_class == "lugs":
        expected = {f"{serial}-lugs-up", f"{serial}-lugs-dn"}
        return None if device_id in expected else "<panel-serial>-lugs-{up,dn}"
    if device_class == "circuit":
        return None if _HEX32.match(device_id) else "<circuit-uuid>"
    if device_class == "mid":
        # Relational on purpose: the MID is correct when it is derived from
        # whatever the BESS id is, even if that BESS id is itself off-pattern.
        # Those are two findings, and conflating them would hide the fix.
        expected = {f"{bess_id}-mid" for bess_id in bess_ids} | {f"{serial}-mid"}
        return None if device_id in expected else "<bess-id>-mid or <panel-serial>-mid"
    if device_class in {"bess", "pv", "evse"}:
        prefix = f"{serial}-"
        conforms = device_id.startswith(prefix) and len(device_id) > len(prefix)
        return None if conforms else "<proxier-id>-<identifier>"
    return None


def device_id_findings(devices: dict[str, dict[str, str]]) -> dict[str, str]:
    """``role -> "<published id> is not <documented pattern>"`` for each departure.

    Keyed by role so the two producers' findings are comparable, and carrying the
    published id in the value so a changed id moves the baseline.
    """
    serial = panel_serial(devices)
    if serial is None:
        return {"<no panel>": "no distribution-enclosure published info/serial-number"}

    bess_ids = {did for did, body in devices.items() if class_of(body) == "bess"}
    findings: dict[str, str] = {}
    for device_id, body in devices.items():
        pattern = _expected_form(class_of(body), device_id, serial, bess_ids)
        if pattern is not None:
            findings[role_of(device_id, body)] = f"{device_id} is not {pattern}"
    return findings
