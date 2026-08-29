"""Device-ID derivation for emitter manifest entries — the only place it happens.

Circuit UUIDs were lifted from publisher.py so the simulator's derivation matches
what the legacy publisher produced: UUID v5 with a fixed namespace, so the same
circuit always yields the same UUID across restarts. The name hashed is
``<panel-serial>/<circuit-id>`` rather than the circuit id alone — see
`stable_circuit_uuid`.

The rest of the device ids live here for a different reason. They follow the
migration guide's Device ID Stability table, because **a Home Assistant entity's
`unique_id` derives from the device id**, and entity survival across the firmware
upgrade is the acceptance criterion this whole simulator exists to make measurable.
A consumer validated against ids no panel publishes has not been validated on the
axis that decides whether a user's history survives.

    Panel         <panel-serial>
    Lugs          <panel-serial>-lugs-{up,dn}
    Circuit       <circuit-uuid>   (uuid5 of <panel-serial>/<circuit-id>)
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


def stable_circuit_uuid(panel_id: str, circuit_id: str) -> str:
    """Deterministic dashless UUID for a circuit, scoped to the panel that owns it.

    Every other id here is already panel-scoped, because every other device is named
    relative to its proxier. A circuit's was not: it hashed the YAML `id` alone, and
    those ids are shared vocabulary across the shipped configs — `MAIN_32.yaml` and
    `MAIN_40.yaml` both have a `solar_inverter` and an `oven`. One panel per broker
    makes that harmless, which is why it survived; two panels in one add-on publish
    to one broker, and the two `solar_inverter`s then claim the same device id and
    the same `ebus/5/<uuid>/...` topics. Both panels write every tick, so each
    circuit's readings alternate between two unrelated loads and a consumer sees
    energy counters that fall as often as they rise.

    Hashing `<panel-serial>/<circuit-id>` scopes the id to its owner the way the
    `<proxier>-<identifier>` forms do. The separator is `/` and not `-` because a
    panel serial may itself contain `-`, and any separator the serial can contain
    admits two different (panel, circuit) pairs hashing the same name.

    Real firmware cannot hit the collision — a panel has its own broker — so this is
    an emulator-only defect. The derivation is nonetheless shared byte-for-byte with
    the flat simulator, which publishes the same circuit ids for the same config: the
    firmware-upgrade rehearsal stops one and starts the other on one panel, and a
    circuit whose id changed at the swap strands its Home Assistant history.
    """
    return str(uuid.uuid5(_CIRCUIT_NAMESPACE, f"{panel_id}/{circuit_id}")).replace("-", "")


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
    return f"{panel_id}-{evse_serial_number(evse, panel_id, idx)}"


def evse_serial_number(evse: Mapping[str, object] | None, panel_id: str, idx: int) -> str:
    """The EVSE's own serial — and it must match what the flat simulator publishes.

    `<panel>-SIM-EVSE-<panel>` reads redundantly once ids take the
    `<proxier>-<identifier>` form, and a real Drive's serial would not embed the
    panel it is attached to. Both true, and both outranked by this: the frozen flat
    simulator publishes `evse/serial-number = SIM-EVSE-<panel-serial>`, and
    `info/serial-number` is the **only** identifier a consumer can use to recognise
    the same physical Drive on both sides of the firmware upgrade — the proxy model
    says so explicitly, because a proxied device id is not stable across the
    proxy-to-native transition.

    So the two simulators must agree on it or the migration harness is comparing two
    different chargers. Briefly changed to `SIM-EVSE-<idx>` for tidiness, which broke
    exactly that; this side is the one that conforms.

    **Lower-case since flat 1.0.16.** That release made the drive serial the flat
    simulator's Homie *node id*, which makes it a topic level rather than only a
    property value — and Homie 5 allows only `a`-`z`, `0`-`9` and `-` there. The
    previous `SIM-EVSE-…` was legal as a value and illegal as an id, so flat could not
    move to meet this side; this side moves. Nothing in the wire format changed here,
    only the case of a simulated serial.

    A *circuit* may carry its own `serial_number`, and that is the form to prefer: the
    positional fallback below derives `-<idx>` from circuit walk order, so reordering
    two drives in a config silently re-keys both of them. See `_evse_circuit_serial`.
    """
    configured = _config_str(evse, "serial_number")
    if configured is not None:
        return configured if idx == 1 else f"{configured}-{idx}"
    return f"sim-evse-{panel_id}" if idx == 1 else f"sim-evse-{panel_id}-{idx}"


def evse_circuit_serial(circuit: Mapping[str, object] | None) -> str | None:
    """A drive's own serial, read from the circuit it feeds.

    Identity belongs to the drive, not to its position in a list. The fallback in
    `evse_serial_number` appends `-<idx>` taken from the order circuits are walked, so
    swapping two `device_type: evse` circuits in a config re-keys both drives: Home
    Assistant builds new devices, the old ones strand with their history, and nothing
    errors. `schema-1`'s own keying function names inventing an ordinal as the thing
    to avoid, and that fallback invents one on the producer side.

    Setting this per circuit removes the ordinal from the answer entirely. It is not
    yet set in the shipped configs, because the flat simulator has no per-circuit
    serial and the two must still agree while the upgrade rehearsal exists. When flat
    retires, the shipped configs should adopt this and the positional fallback should
    go with it.
    """
    return _config_str(circuit, "serial_number")


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
