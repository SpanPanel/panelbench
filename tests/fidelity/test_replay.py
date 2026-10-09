"""A recorded instant moves onto the reproduction's devices by where they are."""

from __future__ import annotations

import pytest
from ebus_panel_sim import DeviceInstance, DeviceManifest, PanelEnvelopeTick, TickInputs

from .replay import replayed


def _manifest(prefix: str) -> DeviceManifest:
    def circuit(n: str, tabs: str, name: str) -> DeviceInstance:
        return DeviceInstance("circuit", f"{prefix}{n}", name, {"tab-numbers": tabs})

    return DeviceManifest(
        instances=(
            circuit("1", "1", "Lights"),
            circuit("2", "3,5", "Charger"),
            DeviceInstance("evse", f"{prefix}drive", "drive", {"feed": f"{prefix}2"}),
            DeviceInstance("bess", f"{prefix}battery", "battery", {}),
        )
    )


def test_each_reading_moves_to_the_device_in_the_same_place() -> None:
    recorded = TickInputs(
        current_time=1.0,
        grid_online=True,
        circuits={"a1": 15.5, "a2": 7200.0},
        evse={"adrive": 7200.0},
        bess_communication={"abattery": "LOST"},
    )
    envelope = PanelEnvelopeTick(wifi_ssid="own")

    tick = replayed(recorded, _manifest("a"), _manifest("b"), envelope)

    assert tick.circuits == {"b1": 15.5, "b2": 7200.0}
    assert tick.evse == {"bdrive": 7200.0}
    assert tick.bess_communication == {"bbattery": "LOST"}
    assert (tick.current_time, tick.grid_online, tick.envelope) == (1.0, True, envelope)


def test_a_reading_for_a_device_the_reproduction_lacks_is_refused() -> None:
    recorded = TickInputs(current_time=1.0, grid_online=True, circuits={"a9": 1.0})
    extra = DeviceManifest(
        instances=(*_manifest("a").instances, DeviceInstance("circuit", "a9", "Pump", {})),
    )

    with pytest.raises(KeyError):
        replayed(recorded, extra, _manifest("b"), PanelEnvelopeTick())
