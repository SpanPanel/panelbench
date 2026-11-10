"""A panel that limits how often a client registers, for rehearsing a client against it.

No capture shows a refused registration, so the behaviour is the one a client is told
to expect: 429 once a client has registered too often in a minute, with or without
``Retry-After``. Marked ``spec_only`` for that.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.bootstrap import BootstrapHttpServer
from panelbench.registration_limit import (
    RegistrationLimit,
    RegistrationLimiter,
    registration_limit,
)
from panelbench.validation import validate_panel_config

pytestmark = pytest.mark.spec_only


def _server(limit: RegistrationLimit | None) -> BootstrapHttpServer:
    certs = MagicMock()
    schema = MagicMock()
    schema.raw_json = "{}"
    return BootstrapHttpServer(
        serial="sim-test-001",
        firmware="sim/v0.1.0",
        certs=certs,
        schema=schema,
        registration_limit=limit,
    )


@pytest.mark.parametrize("retry_after", [True, False])
@pytest.mark.asyncio
async def test_a_client_past_the_limit_is_refused_with_or_without_retry_after(
    retry_after: bool,
) -> None:
    server = _server(RegistrationLimit(per_minute=2, retry_after=retry_after))
    async with TestClient(TestServer(server._app)) as client:
        first = await client.post("/api/v2/auth/register", json={})
        second = await client.post("/api/v2/auth/register", json={})
        refused = await client.post("/api/v2/auth/register", json={})

    assert (first.status, second.status, refused.status) == (200, 200, 429)
    if retry_after:
        assert 1 <= int(refused.headers["Retry-After"]) <= 60
    else:
        assert "Retry-After" not in refused.headers


@pytest.mark.asyncio
async def test_a_panel_with_no_limit_registers_every_time() -> None:
    server = _server(None)
    async with TestClient(TestServer(server._app)) as client:
        statuses = [(await client.post("/api/v2/auth/register", json={})).status for _ in range(5)]

    assert statuses == [200] * 5


def test_a_slot_frees_a_minute_after_the_registration_that_took_it() -> None:
    now = [0.0]
    limiter = RegistrationLimiter(RegistrationLimit(per_minute=1), clock=lambda: now[0])

    assert limiter.refusal("client") is None
    now[0] = 20.0
    assert limiter.refusal("client") == 40
    assert limiter.refusal("other") is None
    now[0] = 60.0
    assert limiter.refusal("client") is None


def test_a_limit_that_is_no_positive_number_is_refused() -> None:
    panel = {"serial_number": "sim", "total_tabs": 32, "main_size": 200}

    assert registration_limit(panel) is None
    with pytest.raises(ValueError, match="positive integer"):
        validate_panel_config({**panel, "registration_limit": {"per_minute": 0}})
