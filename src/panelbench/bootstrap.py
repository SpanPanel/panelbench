"""Bootstrap HTTP server — single-panel per instance.

Each simulated panel gets its own ``BootstrapHttpServer`` bound to a
unique port, matching real SPAN hardware where each panel is a separate
device on a different IP.

The same endpoints are served twice, as hardware serves them: in plaintext
on the bootstrap port, and over TLS on a second port using the panel's own
certificate.  The split is what a client's pinning flow needs — it reads
the CA in plaintext because it has nothing to trust yet, then sends the
registration passphrase over the TLS port under that anchor.

Endpoints:
  GET  /api/v2/status           -> panel identity (serialNumber, firmwareVersion)
  POST /api/v2/auth/register    -> JWT + MQTT credentials (camelCase fields)
  GET  /api/v2/certificate/ca   -> self-signed CA PEM
  GET  /api/v2/homie/schema     -> Homie property schema JSON
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import ssl
import time
from typing import TYPE_CHECKING

from aiohttp import web

from panelbench.const import (
    DEFAULT_BROKER_PASSWORD,
    DEFAULT_BROKER_USERNAME,
    DEFAULT_HTTPS_PORT,
    MQTTS_PORT,
    PATH_CA_CERT,
    PATH_HOMIE_SCHEMA,
    PATH_REGISTER,
    PATH_STATUS,
    WS_PORT,
    WSS_PORT,
)

if TYPE_CHECKING:
    from panelbench.certs import CertificateBundle
    from panelbench.schema import HomieSchemaRegistry

_LOGGER = logging.getLogger(__name__)


class BootstrapHttpServer:
    """HTTP server for a single panel's eBus bootstrap endpoints."""

    def __init__(
        self,
        serial: str,
        firmware: str,
        certs: CertificateBundle,
        schema: HomieSchemaRegistry,
        *,
        broker_username: str = DEFAULT_BROKER_USERNAME,
        broker_password: str = DEFAULT_BROKER_PASSWORD,
        broker_host: str = "localhost",
        host: str = "0.0.0.0",
        port: int = 443,
        https_port: int = DEFAULT_HTTPS_PORT,
    ) -> None:
        self._serial = serial
        self._firmware = firmware
        self._certs = certs
        self._broker_username = broker_username
        self._broker_password = broker_password
        self._broker_host = broker_host
        self._host = host
        self._port = port
        self._https_port = https_port

        self._homie_schema = schema.raw_json
        self._app = web.Application()
        self._runner: web.AppRunner | None = None

        # Bootstrap endpoints
        self._app.router.add_get(PATH_STATUS, self._handle_status)
        self._app.router.add_post(PATH_REGISTER, self._handle_register)
        self._app.router.add_get(PATH_CA_CERT, self._handle_ca_cert)
        self._app.router.add_get(PATH_HOMIE_SCHEMA, self._handle_schema)

    # ------------------------------------------------------------------
    # Bootstrap handlers — field names match real SPAN v2 API
    # ------------------------------------------------------------------

    async def _handle_status(self, _request: web.Request) -> web.Response:
        """GET /api/v2/status — return this panel's identity.

        The ``?serial=`` query parameter is accepted but ignored — each
        server only knows about one panel.

        Response matches real panel: ``{"serialNumber": "...", "firmwareVersion": "..."}``
        """
        return web.json_response(
            {
                "serialNumber": self._serial,
                "firmwareVersion": self._firmware,
                "proximityProven": True,
            }
        )

    async def _handle_register(self, request: web.Request) -> web.Response:
        """POST /api/v2/auth/register — return MQTT credentials.

        Accepts optional ``hopPassphrase`` in the request body (ignored
        by the simulator — any passphrase is accepted).

        Response matches real panel's camelCase field names exactly.
        """
        body: dict[str, str] = {}
        with contextlib.suppress(Exception):
            body = await request.json()

        token = f"sim.{secrets.token_urlsafe(32)}.{secrets.token_urlsafe(16)}"
        passphrase = body.get("hopPassphrase", "sim-passphrase")

        # The broker host returned to the client must be the address the
        # client used to reach *us* — on a real panel the broker is co-located
        # with the HTTP server, so the client connects to the same IP for both.
        broker_host = request.host.split(":")[0] if request.host else self._broker_host

        payload: dict[str, object] = {
            "accessToken": token,
            "tokenType": "Bearer",
            "iatMs": int(time.time() * 1000),
            "ebusBrokerUsername": self._broker_username,
            "ebusBrokerPassword": self._broker_password,
            "ebusBrokerHost": broker_host,
            "ebusBrokerMqttsPort": MQTTS_PORT,
            "ebusBrokerWsPort": WS_PORT,
            "ebusBrokerWssPort": WSS_PORT,
            "hostname": f"span-sim-{self._serial}",
            "serialNumber": self._serial,
            "hopPassphrase": passphrase,
        }

        return web.json_response(payload)

    async def _handle_ca_cert(self, _request: web.Request) -> web.Response:
        return web.Response(
            body=self._certs.ca_cert_pem,
            content_type="application/x-pem-file",
        )

    async def _handle_schema(self, _request: web.Request) -> web.Response:
        return web.Response(
            text=self._homie_schema,
            content_type="application/json",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _server_ssl_context(self) -> ssl.SSLContext:
        """Build the TLS context for the HTTPS listener.

        Uses the same bundle whose CA is handed out by
        ``/api/v2/certificate/ca``, so a client that pins what it fetched
        there can validate what it connects to here.  A bundle whose leaf
        did not chain to that CA would be a simulator that cannot be pinned.
        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self._certs.server_cert_path, self._certs.server_key_path)
        return context

    async def start(self) -> None:
        """Start the plaintext and TLS listeners for this panel.

        Both serve the same application. A failure to bind either one stops
        the server rather than leaving a half-started one behind, because the
        listener that did bind would otherwise hold its port against the
        caller's retry on the next port pair.
        """
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, self._host, self._port).start()
            await web.TCPSite(
                self._runner,
                self._host,
                self._https_port,
                ssl_context=self._server_ssl_context(),
            ).start()
        except (OSError, ssl.SSLError):
            await self.stop()
            raise
        _LOGGER.info(
            "Bootstrap server for %s listening on %s:%d (http) and %s:%d (https)",
            self._serial,
            self._host,
            self._port,
            self._host,
            self._https_port,
        )

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def __aenter__(self) -> BootstrapHttpServer:
        await self.start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.stop()
