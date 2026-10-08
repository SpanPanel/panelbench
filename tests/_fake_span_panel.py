"""A SPAN panel's v2 REST API, enough of it to register with, and a stand-in scrape.

The REST half is real HTTP, so registration runs exactly as it does against a
panel: it is the half whose calls the tests count, since every registration leaves
a client on the real panel that its owner has to remove by hand. The broker half is
replaced: ``fake_scrape`` stands in for ``scraper.scrape_ebus``, recording the
credentials it was handed and returning the captured MAIN 32's tree.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from panelbench import scraper
from panelbench.scraper import PanelCredentials, ScrapedPanel, ScrapeError
from tests._helpers import (
    CAPTURED_MAIN_32,
    CAPTURED_MAIN_32_SERIAL,
    discovered_from_tree_snapshot,
)

FAKE_CA = b"-----BEGIN CERTIFICATE-----\nfake panel ca\n-----END CERTIFICATE-----\n"


@dataclass
class FakeSpanPanel:
    """The panel's REST API, and what was asked of it."""

    serial: str = CAPTURED_MAIN_32_SERIAL
    registrations: list[dict[str, str]] = field(default_factory=list)
    ca_requests: int = 0
    host: str = ""

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/v2/status", self._status)
        app.router.add_post("/api/v2/auth/register", self._register)
        app.router.add_get("/api/v2/certificate/ca", self._ca)
        return app

    async def _status(self, _request: web.Request) -> web.Response:
        return web.json_response({"serialNumber": self.serial, "proximityProven": True})

    async def _register(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.registrations.append(body)
        # A fresh user per registration, as a panel issues one per registered client.
        return web.json_response(
            {
                "ebusBrokerUsername": f"registered-user-{len(self.registrations)}",
                "ebusBrokerPassword": "registered-broker-password",
                "ebusBrokerHost": "127.0.0.1",
                "ebusBrokerMqttsPort": 8883,
                "serialNumber": self.serial,
            }
        )

    async def _ca(self, _request: web.Request) -> web.Response:
        self.ca_requests += 1
        return web.Response(body=FAKE_CA)


@asynccontextmanager
async def running(panel: FakeSpanPanel) -> AsyncIterator[FakeSpanPanel]:
    """*panel*'s API served on a local port, with ``panel.host`` set to reach it."""
    server = TestServer(panel.app())
    await server.start_server()
    panel.host = f"{server.host}:{server.port}"
    try:
        yield panel
    finally:
        await server.close()


@dataclass
class FakeScrape:
    """Stands in for ``scraper.scrape_ebus``: records each attempt, refuses some users."""

    attempts: list[tuple[PanelCredentials, bytes]] = field(default_factory=list)
    refused_users: set[str] = field(default_factory=set)

    async def __call__(
        self, creds: PanelCredentials, ca_pem: bytes, *, status_callback: object = None
    ) -> ScrapedPanel:
        del status_callback
        self.attempts.append((creds, ca_pem))
        if creds.username in self.refused_users:
            raise ScrapeError("scraping", f"broker refused {creds.username}")
        return ScrapedPanel(
            serial_number=creds.serial_number,
            devices=discovered_from_tree_snapshot(CAPTURED_MAIN_32),
            mqtts_port=creds.mqtts_port,
            ca_pem=ca_pem,
        )

    @property
    def users(self) -> list[str]:
        return [creds.username for creds, _ in self.attempts]


def install_fake_scrape(monkeypatch: pytest.MonkeyPatch) -> FakeScrape:
    """Replace the broker half of a scrape for the rest of the test."""
    fake = FakeScrape()
    monkeypatch.setattr(scraper, "scrape_ebus", fake)
    return fake
