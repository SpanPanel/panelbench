"""Tests for the single-panel BootstrapHttpServer."""

from __future__ import annotations

import hashlib
import json
import socket
import ssl
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.bootstrap import BootstrapHttpServer
from panelbench.certs import generate_certificates
from panelbench.const import DEFAULT_FIRMWARE_VERSION
from panelbench.schema import load_schema, render_for_panel


def _make_server() -> BootstrapHttpServer:
    """Create a BootstrapHttpServer with mocked certs and schema."""
    certs = MagicMock()
    certs.ca_cert_pem = b"-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"

    schema = MagicMock()
    schema.raw_json = '{"test": true}'

    return BootstrapHttpServer(
        serial="sim-test-001",
        firmware=DEFAULT_FIRMWARE_VERSION,
        certs=certs,
        schema=schema,
        broker_username="span",
        broker_password="sim-password",
        broker_host="localhost",
    )


async def test_status_returns_single_panel() -> None:
    """GET /api/v2/status returns the one panel."""
    server = _make_server()
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/api/v2/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["serialNumber"] == "sim-test-001"
        assert data["firmwareVersion"] == DEFAULT_FIRMWARE_VERSION
        assert data["proximityProven"] is True


async def test_status_ignores_serial_query_param() -> None:
    """?serial= has no effect — always returns the one panel."""
    server = _make_server()
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/api/v2/status?serial=other-panel")
        assert resp.status == 200
        data = await resp.json()
        assert data["serialNumber"] == "sim-test-001"


async def test_register_returns_broker_details() -> None:
    """POST /api/v2/auth/register returns MQTT creds."""
    server = _make_server()
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/api/v2/auth/register", json={})
        assert resp.status == 200
        data = await resp.json()
        assert "accessToken" in data
        assert data["ebusBrokerUsername"] == "span"
        assert data["ebusBrokerPassword"] == "sim-password"
        assert data["serialNumber"] == "sim-test-001"
        assert data["hostname"] == "span-sim-sim-test-001"


async def test_ca_cert_endpoint() -> None:
    """GET /api/v2/certificate/ca returns PEM."""
    server = _make_server()
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/api/v2/certificate/ca")
        assert resp.status == 200
        assert resp.content_type == "application/x-pem-file"
        body = await resp.read()
        assert b"BEGIN CERTIFICATE" in body


async def test_schema_endpoint() -> None:
    """GET /api/v2/homie/schema returns JSON."""
    server = _make_server()
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/api/v2/homie/schema")
        assert resp.status == 200
        assert resp.content_type == "application/json"
        data = await resp.json()
        assert data == {"test": True}


async def test_no_admin_endpoints() -> None:
    """/admin/panels and /admin/reload return 404."""
    server = _make_server()
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/admin/panels")
        assert resp.status == 404

        resp = await client.post("/admin/reload")
        assert resp.status == 404


async def test_schema_endpoint_serves_40_tab_format() -> None:
    """Bootstrap HTTP endpoint serves a rendered schema whose space.format matches panel size."""
    template = load_schema(
        Path(__file__).parent.parent / "src" / "panelbench" / "data" / "homie_schema.json"
    )
    rendered = render_for_panel(template, 40)

    certs = MagicMock()
    certs.ca_cert_pem = b"-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"

    server = BootstrapHttpServer(
        serial="sim-40t-001",
        firmware=DEFAULT_FIRMWARE_VERSION,
        certs=certs,
        schema=rendered,
        broker_username="span",
        broker_password="sim-password",
        broker_host="localhost",
    )

    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/api/v2/homie/schema")
        assert resp.status == 200
        data = await resp.json()
        assert data["types"]["energy.ebus.device.circuit"]["space"]["format"] == "1:40:1"
        # Hash is content-derived, not the stamped-in-template value
        expected_hash = (
            "sha256:"
            + hashlib.sha256(json.dumps(data["types"], sort_keys=True).encode()).hexdigest()[:16]
        )
        assert data["typesSchemaHash"] == expected_hash


def _free_port() -> int:
    """Return a port nothing is listening on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _client_context(ca_pem: bytes) -> ssl.SSLContext:
    """Build a client context that trusts only the panel's CA.

    ``VERIFY_X509_STRICT`` is cleared for the same reason the integration
    clears it: the CA carries no Subject Key Identifier and the leaf no
    Authority Key Identifier, matching hardware, and strict verification
    rejects that outright.
    """
    context = ssl.create_default_context(cadata=ca_pem.decode())
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


async def test_serves_tls_with_a_leaf_the_published_ca_signs(tmp_path: Path) -> None:
    """The HTTPS listener presents a certificate that chains to the published CA.

    This is the check a pinning client makes before it sends anything secret:
    fetch the CA in plaintext, then refuse to trust it unless it validates the
    certificate the panel actually serves. A simulator that only listened in
    plaintext could not be set up by such a client at all.
    """
    certs = generate_certificates(tmp_path)
    schema = MagicMock()
    schema.raw_json = '{"test": true}'

    http_port = _free_port()
    https_port = _free_port()
    server = BootstrapHttpServer(
        serial="sim-test-001",
        firmware=DEFAULT_FIRMWARE_VERSION,
        certs=certs,
        schema=schema,
        host="127.0.0.1",
        port=http_port,
        https_port=https_port,
    )

    await server.start()
    try:
        # The CA comes over plaintext, as it must: the client has nothing to
        # trust yet at the moment it asks for the anchor.
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{http_port}/api/v2/certificate/ca") as resp,
        ):
            assert resp.status == 200
            ca_pem = await resp.read()

        context = _client_context(ca_pem)
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"https://localhost:{https_port}/api/v2/status", ssl=context) as resp,
        ):
            assert resp.status == 200
            assert (await resp.json())["serialNumber"] == "sim-test-001"
    finally:
        await server.stop()


async def test_tls_rejects_a_client_that_does_not_trust_the_panel_ca(tmp_path: Path) -> None:
    """A client trusting only the public roots cannot reach the TLS listener."""
    certs = generate_certificates(tmp_path)
    schema = MagicMock()
    schema.raw_json = "{}"

    http_port = _free_port()
    https_port = _free_port()
    server = BootstrapHttpServer(
        serial="sim-test-001",
        firmware=DEFAULT_FIRMWARE_VERSION,
        certs=certs,
        schema=schema,
        host="127.0.0.1",
        port=http_port,
        https_port=https_port,
    )

    await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                await session.get(f"https://localhost:{https_port}/api/v2/status")
    finally:
        await server.stop()


async def test_a_bound_https_port_does_not_leave_the_http_one_held(tmp_path: Path) -> None:
    """A half-started server releases the listener that did bind.

    The caller's response to ``EADDRINUSE`` is to retry on the next port pair;
    a plaintext listener left running from the failed attempt would hold its
    port and turn one collision into a permanent one.
    """
    certs = generate_certificates(tmp_path)
    schema = MagicMock()
    schema.raw_json = "{}"

    http_port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken_https_port = int(blocker.getsockname()[1])

    server = BootstrapHttpServer(
        serial="sim-test-001",
        firmware=DEFAULT_FIRMWARE_VERSION,
        certs=certs,
        schema=schema,
        host="127.0.0.1",
        port=http_port,
        https_port=taken_https_port,
    )

    try:
        with pytest.raises(OSError, match=r"[Aa]ddress"):
            await server.start()

        # The HTTP port is free again — proven by binding it, not by asking.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            probe.bind(("127.0.0.1", http_port))
    finally:
        blocker.close()
        await server.stop()
