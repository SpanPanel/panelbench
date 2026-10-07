"""Which PV circuit the top-level ``pv`` section describes.

A real panel publishes the inverter its feeding circuit names in
``connection/feeds-device-id``, so the section's identity, and its rating, follow a
circuit, never a position in the list. Read here from the config as YAML hands it
over, because the loader needs the answer before validation has typed the config,
and ``spec_generator.pv_section_circuit`` builds on it afterwards, so the two cannot
disagree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from panelbench.firmware import SPAN_RELEASE_202639, panel_firmware_version, predates

if TYPE_CHECKING:
    from collections.abc import Mapping


def pv_circuit_ids(config: Mapping[str, object]) -> list[str]:
    """The ids of *config*'s PV circuits, those whose template says ``device_type: pv``.

    In list order. Anything malformed is skipped rather than refused: validation
    names it.
    """
    templates = config.get("circuit_templates")
    circuits = config.get("circuits")
    if not isinstance(templates, dict) or not isinstance(circuits, list):
        return []
    ids: list[str] = []
    for circuit in circuits:
        if not isinstance(circuit, dict) or "id" not in circuit:
            continue
        template_name = circuit.get("template")
        template = templates.get(template_name) if isinstance(template_name, str) else None
        if isinstance(template, dict) and template.get("device_type") == "pv":
            ids.append(str(circuit["id"]))
    return ids


def bound_pv_circuit_id(config: Mapping[str, object]) -> str | None:
    """The id of the PV circuit feeding the inverter the ``pv`` section describes.

    The one ``pv.feed`` names, else the panel's only PV circuit. ``pv.feed`` names a
    circuit by its ``id`` under ``circuits``. Not by the device id the circuit
    publishes, which follows the serial the engine settles on after validation (a
    ``sim-`` prefix, or an override), so a config cannot rely on it.

    With several PV circuits and no ``pv.feed`` the section describes none of them,
    and each inverter is named by its own circuit. A panel before SPAN release
    202639 publishes one PV device for all of them, fed by one circuit
    (SPAN-API-Client-Docs CHANGELOG, Release 202639), so a config naming such a
    release must say which.

    Raises:
        ValueError: ``pv.feed`` names no PV circuit, or a config naming a release
            before 202639 has several PV circuits and no ``pv.feed``.
    """
    pv_ids = pv_circuit_ids(config)
    pv_cfg = config.get("pv")
    if isinstance(pv_cfg, dict) and "feed" in pv_cfg:
        return _pv_circuit_named(config, pv_ids, str(pv_cfg["feed"]))
    if len(pv_ids) == 1:
        return pv_ids[0]
    firmware = panel_firmware_version(config)
    if len(pv_ids) > 1 and predates(firmware, SPAN_RELEASE_202639):
        ids = ", ".join(repr(circuit_id) for circuit_id in pv_ids)
        raise ValueError(
            f"This panel has PV circuits {ids}, and firmware_version {firmware!r} names a "
            "release before 202639, which publishes one solar device fed by one of them: "
            "set pv.feed to the id of the circuit feeding the inverter the pv section describes"
        )
    return None


def _pv_circuit_named(config: Mapping[str, object], pv_ids: list[str], feed: str) -> str:
    """*feed*, once it is known to name a PV circuit by its config ``id``."""
    circuits = config.get("circuits")
    named = isinstance(circuits, list) and any(
        isinstance(circuit, dict) and str(circuit.get("id")) == feed for circuit in circuits
    )
    if not named:
        raise ValueError(
            f"pv.feed {feed!r} names no circuit: set it to the id of the PV circuit feeding "
            "the inverter the pv section describes"
        )
    if feed not in pv_ids:
        raise ValueError(
            f"pv.feed names circuit {feed!r}, which is not a PV circuit: its template "
            "has no device_type: pv"
        )
    return feed
