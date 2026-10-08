"""Discovering a panel's tree from a real broker, as a scrape of a live panel does."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiomqtt
import pytest

from panelbench.scraper import BrokerRefused, _discover_tree, _validate_discovered_tree
from tests.integration._mosquitto import MOSQUITTO, Mosquitto, requires_mosquitto

pytestmark = requires_mosquitto


async def _publish_panel_slowly(port: int, serial: str, circuits: list[str]) -> None:
    """A panel whose tree arrives a piece at a time, each piece after a gap longer
    than the scrape's quiet period, and the first only after another such gap."""
    children = [f"{serial}-{c}" for c in circuits]
    await asyncio.sleep(0.5)
    async with aiomqtt.Client("127.0.0.1", port, identifier=f"{serial}-publisher") as panel:
        root = {
            "homie": "5.0",
            "name": serial,
            "type": "energy.ebus.device.distribution-enclosure",
            "children": children,
            "nodes": {},
        }
        await panel.publish(f"ebus/5/{serial}/$state", b"ready", qos=1, retain=True)
        await panel.publish(f"ebus/5/{serial}/$description", json.dumps(root), qos=1, retain=True)
        for index, child in enumerate(children, start=1):
            await asyncio.sleep(0.5)
            description = {
                "homie": "5.0",
                "name": child,
                "type": "energy.ebus.device.circuit",
                "root": serial,
                "parent": serial,
                "nodes": {
                    "info": {
                        "name": "info",
                        "properties": {"spaces": {"name": "spaces", "datatype": "string"}},
                    }
                },
            }
            await panel.publish(f"ebus/5/{child}/$state", b"ready", qos=1, retain=True)
            await panel.publish(
                f"ebus/5/{child}/$description", json.dumps(description), qos=1, retain=True
            )
            await panel.publish(f"ebus/5/{child}/info/spaces", str(index), qos=1, retain=True)


@pytest.mark.asyncio
async def test_a_tree_slow_to_arrive_is_scraped_whole(mosquitto: Mosquitto) -> None:
    """The quiet period starts at the first message, and the scrape waits for every
    device the panel declares, so neither a slow start nor a slow tree is cut short."""
    serial = "example-panel-001"
    circuits = ["c1", "c2", "c3", "c4"]
    publisher = asyncio.create_task(_publish_panel_slowly(mosquitto.port, serial, circuits))

    devices = await _discover_tree(
        host="127.0.0.1",
        port=mosquitto.port,
        root=serial,
        username=None,
        password=None,
        ca_cert_path=None,
        connect_timeout=5.0,
        stability_timeout=0.2,
        max_timeout=15.0,
        status_callback=None,
    )
    await publisher

    _validate_discovered_tree(devices, serial)
    for index, circuit in enumerate(circuits, start=1):
        device = devices[f"{serial}-{circuit}"]
        assert device.description is not None
        assert device.get_property("info", "spaces") == str(index)


@pytest.mark.asyncio
async def test_credentials_the_broker_refuses_are_a_refusal(tmp_path: Path) -> None:
    """The one failure a new registration can fix, told apart from every other."""
    assert MOSQUITTO is not None
    broker = Mosquitto(MOSQUITTO, tmp_path, allow_anonymous=False)
    await broker.start()
    try:
        with pytest.raises(BrokerRefused):
            await _discover_tree(
                host="127.0.0.1",
                port=broker.port,
                root="example-panel-001",
                username="example-user",
                password="wrong-password",
                ca_cert_path=None,
                connect_timeout=5.0,
                stability_timeout=0.2,
                max_timeout=2.0,
                status_callback=None,
            )
    finally:
        await broker.stop()
