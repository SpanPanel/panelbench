"""Tests for the mDNS records a simulated panel advertises."""

from __future__ import annotations

from unittest.mock import AsyncMock

from panelbench.const import DEFAULT_FIRMWARE_VERSION
from panelbench.discovery import SERVICE_TYPE_EBUS, PanelAdvertiser


def _decoded_ebus_properties(advertiser: PanelAdvertiser, serial: str) -> dict[str, str]:
    """Return the eBus TXT record advertised for *serial*, as text."""
    infos = advertiser._services[serial]
    ebus = next(info for info in infos if info.type == SERVICE_TYPE_EBUS)
    return {
        key.decode() if isinstance(key, bytes) else key: (
            value.decode() if isinstance(value, bytes) else str(value)
        )
        for key, value in (ebus.properties or {}).items()
    }


async def test_moved_ports_are_both_advertised() -> None:
    """A panel on non-standard ports publishes both of them.

    A client that learns only the HTTP port can fetch the CA and then has
    nowhere to check it against; the TLS port is what turns a fetched
    certificate into one it can prove the panel is serving.
    """
    advertiser = PanelAdvertiser()
    advertiser._zeroconf = AsyncMock()

    await advertiser.register_panel(
        "sim-001", DEFAULT_FIRMWARE_VERSION, port=8081, https_port=9081
    )

    props = _decoded_ebus_properties(advertiser, "sim-001")
    assert props["httpPort"] == "8081"
    assert props["httpsPort"] == "9081"


async def test_standard_ports_are_left_out_of_the_txt_record() -> None:
    """Nothing is published for ports that never moved.

    A record naming 80 and 443 says the same thing as no record at all, and
    hardware on standard ports advertises neither.
    """
    advertiser = PanelAdvertiser()
    advertiser._zeroconf = AsyncMock()

    await advertiser.register_panel("sim-002", DEFAULT_FIRMWARE_VERSION, port=80, https_port=443)

    props = _decoded_ebus_properties(advertiser, "sim-002")
    assert "httpPort" not in props
    assert "httpsPort" not in props
