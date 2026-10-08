"""PanelBench registers with a source panel once, and reuses what that returned.

Every registration adds a client to the real panel that its owner has to remove by
hand. Each clone, sync and restore used to register a new one under a random name,
so a panel cloned and synced a few times collected a list of them. A panel's
registration is now made once, under a name derived from its serial, and the
credentials it returns are kept and reused; credentials a user already has can be
given instead of registering at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from panelbench.panel_secrets import PanelSecretsStore
from panelbench.scraper import (
    ScrapeError,
    SuppliedBroker,
    registered_client_name,
    scrape_panel,
)
from tests._fake_span_panel import FAKE_CA, FakeSpanPanel, install_fake_scrape, running


def _store(tmp_path: Path) -> PanelSecretsStore:
    return PanelSecretsStore(tmp_path / "secrets" / "panel_sources.json")


@pytest.mark.asyncio
async def test_a_panel_is_registered_once_under_a_name_from_its_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scrape = install_fake_scrape(monkeypatch)
    async with running(FakeSpanPanel()) as panel:
        await scrape_panel(panel.host, _store(tmp_path), passphrase="example-passphrase")
        # A later sync, from a PanelBench restarted since: a fresh store on the file.
        await scrape_panel(panel.host, _store(tmp_path))

    assert [r["name"] for r in panel.registrations] == [registered_client_name(panel.serial)]
    assert panel.registrations[0]["hopPassphrase"] == "example-passphrase"
    assert panel.serial in registered_client_name(panel.serial)
    assert scrape.users == ["registered-user-1", "registered-user-1"]
    broker_hosts = {creds.broker_host for creds, _ in scrape.attempts}
    assert broker_hosts == {panel.host.split(":")[0]}, "a scrape dialled the advertised name"


@pytest.mark.asyncio
async def test_credentials_the_broker_stops_accepting_are_replaced_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the same name, with the passphrase the store kept."""
    scrape = install_fake_scrape(monkeypatch)
    async with running(FakeSpanPanel()) as panel:
        await scrape_panel(panel.host, _store(tmp_path), passphrase="example-passphrase")
        scrape.refused_users.add("registered-user-1")

        await scrape_panel(panel.host, _store(tmp_path))

    assert [r["name"] for r in panel.registrations] == [registered_client_name(panel.serial)] * 2
    assert scrape.users == ["registered-user-1", "registered-user-1", "registered-user-2"]
    broker = _store(tmp_path).get(panel.serial).broker
    assert broker is not None
    assert broker.username == "registered-user-2"


@pytest.mark.asyncio
async def test_refused_credentials_without_a_passphrase_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering without the passphrase would only fail differently."""
    scrape = install_fake_scrape(monkeypatch)
    async with running(FakeSpanPanel()) as panel:
        await scrape_panel(
            panel.host,
            _store(tmp_path),
            supplied=SuppliedBroker(
                serial=panel.serial, username="existing-user", password="pw", port=8883
            ),
        )
        scrape.refused_users.add("existing-user")

        with pytest.raises(ScrapeError, match="existing-user"):
            await scrape_panel(panel.host, _store(tmp_path))

    assert panel.registrations == []


@pytest.mark.asyncio
async def test_supplied_broker_credentials_register_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scrape = install_fake_scrape(monkeypatch)
    async with running(FakeSpanPanel()) as panel:
        scraped = await scrape_panel(
            panel.host,
            _store(tmp_path),
            supplied=SuppliedBroker(
                serial=panel.serial, username="existing-user", password="pw", port=8883
            ),
        )

    assert panel.registrations == []
    [(creds, ca_pem)] = scrape.attempts
    assert (creds.username, creds.password, creds.mqtts_port) == ("existing-user", "pw", 8883)
    assert creds.broker_host == panel.host.split(":")[0]
    assert ca_pem == FAKE_CA, "a CA left blank is fetched from the panel"
    assert scraped.serial_number == panel.serial
    assert _store(tmp_path).get(panel.serial).broker is not None


@pytest.mark.asyncio
async def test_a_supplied_ca_is_used_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scrape = install_fake_scrape(monkeypatch)
    supplied_ca = "-----BEGIN CERTIFICATE-----\nsupplied\n-----END CERTIFICATE-----\n"
    async with running(FakeSpanPanel()) as panel:
        await scrape_panel(
            panel.host,
            _store(tmp_path),
            supplied=SuppliedBroker(
                serial=panel.serial,
                username="existing-user",
                password="pw",
                port=8883,
                ca_pem=supplied_ca,
            ),
        )

    assert panel.ca_requests == 0
    assert scrape.attempts[0][1] == supplied_ca.encode()
