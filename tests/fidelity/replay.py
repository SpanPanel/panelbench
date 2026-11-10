"""The captured instant, replayed on a reproduction of the panel.

A reproduction drives its emitter from its own physics, so its signs at any moment
are its own, not the panel's: a load that happened to backfeed when the panel was
captured, a battery charging then and discharging now. Held to the bar, those read as
differences that are only a different moment. So a cell publishes its first tick from
the readings the capture was recorded with, and compares like for like.

The recorded tick is keyed by the capture definition's device ids, and a reproduction
has its own, so each reading is moved to the device in the same place: a circuit by
its spaces and name, a SPAN Drive by the circuit feeding it, and the battery, of which
a definition and a reproduction have at most one each, to the battery. What the
reproduction's own panel says about itself, its envelope, stays its own.
"""

from __future__ import annotations

from ebus_panel_sim import (
    BESSCommunication,
    DeviceInstance,
    DeviceManifest,
    PanelEnvelopeTick,
    TickInputs,
)


def _circuit_places(manifest: DeviceManifest) -> dict[tuple[str, str], str]:
    """Each circuit's device id by its place: its spaces and its name."""
    return {
        (instance.metadata.get("tab-numbers", ""), instance.display_name): instance.instance_id
        for instance in manifest.of_class("circuit")
    }


def _place_of(manifest: DeviceManifest, device_id: str) -> tuple[str, str]:
    [circuit] = [i for i in manifest.of_class("circuit") if i.instance_id == device_id]
    return circuit.metadata.get("tab-numbers", ""), circuit.display_name


def _fed_by(manifest: DeviceManifest, entity_class: str) -> dict[str, DeviceInstance]:
    """Each device of *entity_class* by the id of the circuit feeding it."""
    return {
        instance.metadata["feed"]: instance
        for instance in manifest.of_class(entity_class)
        if "feed" in instance.metadata
    }


def replayed(
    recorded: TickInputs,
    recorded_from: DeviceManifest,
    reproduction: DeviceManifest,
    envelope: PanelEnvelopeTick,
) -> TickInputs:
    """*recorded*, keyed by *recorded_from*'s ids, moved onto *reproduction*'s.

    Raises ``KeyError`` for a reading whose device the reproduction does not have in
    the same place, which is a difference the bar reports anyway.
    """
    circuits = _circuit_places(reproduction)
    circuit_ids = {
        device_id: circuits[_place_of(recorded_from, device_id)] for device_id in recorded.circuits
    }
    drives = _fed_by(reproduction, "evse")
    drive_ids = {
        drive.instance_id: drives[circuits[_place_of(recorded_from, feed)]].instance_id
        for feed, drive in _fed_by(recorded_from, "evse").items()
    }
    batteries = dict(
        zip(
            (b.instance_id for b in recorded_from.of_class("bess")),
            (b.instance_id for b in reproduction.of_class("bess")),
            strict=True,
        )
    )
    links: dict[str, BESSCommunication] = {
        batteries[battery]: link for battery, link in recorded.bess_communication.items()
    }
    return TickInputs(
        current_time=recorded.current_time,
        grid_online=recorded.grid_online,
        circuits={circuit_ids[i]: power for i, power in recorded.circuits.items()},
        evse={drive_ids[i]: power for i, power in recorded.evse.items()},
        envelope=envelope,
        bess_communication=links,
    )
