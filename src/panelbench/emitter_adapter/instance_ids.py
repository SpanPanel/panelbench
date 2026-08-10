"""Device-ID derivation for emitter manifest entries — the only place it happens.

Circuit UUIDs were lifted from publisher.py so the simulator's derivation matches
what the legacy publisher produced: UUID v5 with a fixed namespace, so the same
circuit_id always yields the same UUID across restarts.

The rest of the device ids live here for a different reason. They follow the
migration guide's Device ID Stability table, because **a Home Assistant entity's
`unique_id` derives from the device id**, and entity survival across the firmware
upgrade is the acceptance criterion this whole simulator exists to make measurable.
A consumer validated against ids no panel publishes has not been validated on the
axis that decides whether a user's history survives.

    Panel         <panel-serial>
    Lugs          <panel-serial>-lugs-{up,dn}
    Circuit       <circuit-uuid>
    BESS/PV/EVSE  <proxier-id>-<identifier>   (proxied; proxier is the panel)
    MID           <bess-id>-mid

`devices/distribution-enclosure.md` Example 1 shows the proxied form concretely:
`xy-0001-aaaaa-lugs-up`, `xy-0001-aaaaa-TG000000000001` (panel + the BESS's own
serial), `xy-0001-aaaaa-pv-1`, `xy-0001-aaaaa-TG000000000001-mid`.

**Single source of truth, deliberately.** `spec_generator` builds the manifest and
`runtime` builds the per-tick inputs and the BESS config, and both need the same
ids — the emitter looks physics up by instance id, so a disagreement is not a
cosmetic drift but a `KeyError`. They previously derived ids independently from the
same config keys, which is the shape of bug that hides until the two defaults
diverge. Both now call these functions.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_CIRCUIT_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def stable_circuit_uuid(circuit_id: str) -> str:
    """Return a deterministic dashless UUID for a circuit identifier."""
    return str(uuid.uuid5(_CIRCUIT_NAMESPACE, circuit_id)).replace("-", "")


def lugs_device_ids(panel_id: str) -> tuple[str, str]:
    """Upstream and downstream lugs device ids, in that order."""
    return f"{panel_id}-lugs-up", f"{panel_id}-lugs-dn"


def bess_device_id(panel_id: str, bess: Mapping[str, object] | None) -> str:
    """`<panel>-<identifier>` for a BESS proxied by this panel."""
    return f"{panel_id}-{_der_identifier(bess, 'bess')}"


def mid_device_id(panel_id: str, bess: Mapping[str, object] | None) -> str:
    """`<bess-id>-mid`, the form used when the MID ships as part of the BESS.

    The enclosure model gives an *enclosure-integrated* MID the id
    `<enclosure-id>-mid` and makes it a direct child of the panel instead. That
    case is not reachable from here: the emitter's `mapping/mid.yaml` pins
    `parent_entity_class: bess`, so only the BESS-child form can be published.
    Correct for the hardware modelled today — an external upstream BESS — and the
    thing to revisit when panels with an incorporated BESS arrive.
    """
    return f"{bess_device_id(panel_id, bess)}-mid"


def pv_device_id(panel_id: str, pv: Mapping[str, object] | None) -> str:
    """`<panel>-<identifier>` for PV proxied by this panel."""
    return f"{panel_id}-{_der_identifier(pv, 'pv-1')}"


def evse_device_id(panel_id: str, evse: Mapping[str, object] | None, idx: int) -> str:
    """`<panel>-<identifier>` for the `idx`-th EVSE (1-based)."""
    explicit = _config_str(evse, "instance_id")
    if explicit is not None:
        return f"{panel_id}-{explicit}" if idx == 1 else f"{panel_id}-{explicit}-{idx}"
    return f"{panel_id}-{evse_serial_number(evse, idx)}"


def evse_serial_number(evse: Mapping[str, object] | None, idx: int) -> str:
    """The EVSE's own serial, which must not embed the panel it is attached to.

    A DER's serial belongs to the DER. The synthetic default used to be
    `SIM-EVSE-<panel-serial>`, which made the proxied device id read
    `<panel>-SIM-EVSE-<panel>` once ids took the `<proxier>-<identifier>` form —
    the panel twice, for a value that is supposed to identify the charger.
    """
    configured = _config_str(evse, "serial_number")
    if configured is not None:
        return configured if idx == 1 else f"{configured}-{idx}"
    return f"SIM-EVSE-{idx:03d}"


def _der_identifier(cfg: Mapping[str, object] | None, default: str) -> str:
    """The DER's own identifier: its serial when known, else an explicit pin.

    Serial first, because that is what the spec's own example uses and what real
    firmware has to hand. `instance_id` remains honoured for a config that
    deliberately pins one, and is the *identifier*, not the whole device id — a
    config cannot opt out of the `<proxier>-<identifier>` shape, which is the
    shape being made faithful.
    """
    for key in ("serial_number", "instance_id"):
        value = _config_str(cfg, key)
        if value is not None:
            return value
    return default


def _config_str(cfg: Mapping[str, object] | None, key: str) -> str | None:
    if not cfg:
        return None
    value = cfg.get(key)
    if value is None or value == "":
        return None
    return str(value)
