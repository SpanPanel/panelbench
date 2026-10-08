"""The aiomqtt and paho internals the broker link reaches into, as installed.

aiomqtt offers no way to withdraw a connect it gave up on, nor to leave a session
without a DISCONNECT, so the link reaches into the paho client aiomqtt wraps. The
link's own tests run on a fake that defines both, so only this notices an upgrade
that moves them: the range pyproject.toml pins is what CI proves, and a bump of
either package fails here before it ships.
"""

from __future__ import annotations

import aiomqtt
import paho.mqtt.client as mqtt
import pytest


@pytest.mark.asyncio
async def test_an_aiomqtt_client_wraps_a_paho_client_the_link_can_withdraw_and_drop() -> None:
    client = aiomqtt.Client("127.0.0.1")

    assert isinstance(client._client, mqtt.Client)
    assert callable(client._client.disconnect)
    assert callable(client._client._sock_close)
