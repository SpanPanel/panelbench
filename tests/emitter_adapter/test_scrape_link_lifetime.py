"""A scrape's broker link never outlives the scrape.

The link supervises itself, so one left open reconnects for the life of the process,
with the source panel's broker credentials. Whatever fails after it connects, the
scrape closes it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from panelbench import scraper

if TYPE_CHECKING:
    from tests.emitter_adapter._fake_aiomqtt import FakeBroker


@pytest.mark.asyncio
async def test_a_failure_right_after_connecting_closes_the_link(
    fake_broker: FakeBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_controller(**_kwargs: object) -> object:
        raise RuntimeError("the controller could not be built")

    monkeypatch.setattr(scraper.ebus_sdk, "Controller", broken_controller)

    with pytest.raises(RuntimeError, match="could not be built"):
        await scraper._discover_tree(
            host="broker.invalid",
            port=8883,
            root="example-panel-001",
            username=None,
            password=None,
            ca_cert_path=None,
            connect_timeout=1.0,
            stability_timeout=0.1,
            max_timeout=1.0,
            status_callback=None,
        )

    [client] = fake_broker.clients
    assert client.exited, "the scrape's broker connection was left open"
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("mqtt-link")]
