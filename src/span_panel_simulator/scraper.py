"""eBus scraper — connect to a real SPAN panel and discover its device tree.

Performs the authentication handshake via the panel's v2 REST API, then drives a
tree-rooted ``ebus_sdk.Controller`` against the panel's MQTTS broker until the
retained burst stabilises.

Discovery is delegated rather than hand-rolled because under the parent/child data
model a panel's circuits, BESS, PV, EVSE, lugs and MID are SEPARATE Homie devices in
sibling namespaces. Tree membership is declared in each device's ``$description``
(``root`` / ``parent``), not implied by topic prefix, so a wildcard subscription
cannot express "this panel's devices" — see ``_discover_tree``.

This module still imports no span-panel-api or HA integration code. It uses
``aiohttp`` for the REST handshake and ``ebus_sdk`` for discovery.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import ebus_sdk
from ebus_sdk import DiscoveredDevice

_LOGGER = logging.getLogger(__name__)

# Timeouts
_STABILITY_TIMEOUT_S = 5.0
_MAX_SCRAPE_TIMEOUT_S = 30.0
_HTTP_TIMEOUT_S = 15.0
# How often to re-read the tree while waiting for the retained burst to settle.
_POLL_INTERVAL_S = 0.25

# Homie $type of a circuit device, used to sanity-check a discovered tree.
TYPE_CIRCUIT = "energy.ebus.device.circuit"

# Status callback type: async (phase, detail) -> None
StatusCallback = Callable[[str, str], Awaitable[None]]


class ScrapeError(Exception):
    """Raised when the scrape pipeline encounters a recoverable error."""

    def __init__(self, phase: str, message: str) -> None:
        self.phase = phase
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PanelCredentials:
    """MQTT credentials and identity returned by the panel's /register endpoint."""

    username: str
    password: str
    serial_number: str
    mqtts_port: int
    broker_host: str


@dataclass(frozen=True, slots=True)
class ScrapedPanel:
    """Result of a successful eBus scrape."""

    serial_number: str
    #: Every device in the panel's tree, keyed by Homie device id — the panel itself
    #: plus its circuits, BESS, PV, EVSE, lugs and MID. Replaces the flat scrape's
    #: ``properties`` topic map and ``description`` blob: the SDK already resolved
    #: topics and tree membership during discovery, so re-deriving either here would
    #: be a second implementation of a rule that already has one.
    devices: dict[str, DiscoveredDevice]
    mqtts_port: int
    ca_pem: bytes = field(repr=False)


async def register_with_panel(
    host: str,
    passphrase: str | None,
) -> tuple[PanelCredentials, bytes]:
    """Authenticate with a real SPAN panel and retrieve MQTT credentials.

    Args:
        host: IP or hostname of the panel.
        passphrase: Panel passphrase (None for door-bypass).

    Returns:
        A tuple of (PanelCredentials, ca_pem_bytes).

    Raises:
        ScrapeError: On network or authentication failure.
    """
    register_url = f"http://{host}/api/v2/auth/register"
    ca_url = f"http://{host}/api/v2/certificate/ca"
    client_name = f"sim-clone-{uuid.uuid4()}"

    body: dict[str, str] = {"name": client_name}
    if passphrase is not None:
        body["hopPassphrase"] = passphrase

    timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_S)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Step 1: Register for MQTT credentials
            async with session.post(register_url, json=body) as resp:
                if resp.status in (401, 403):
                    raise ScrapeError("registering", "Bad passphrase or access denied")
                if resp.status == 422:
                    hint = (
                        "Panel rejected the request (422). "
                        "This usually means a passphrase is required "
                        "— enter the door-code passphrase and retry."
                    )
                    raise ScrapeError("registering", hint)
                resp.raise_for_status()
                data = await resp.json()

            creds = PanelCredentials(
                username=data["ebusBrokerUsername"],
                password=data["ebusBrokerPassword"],
                serial_number=data["serialNumber"],
                mqtts_port=int(data["ebusBrokerMqttsPort"]),
                broker_host=data.get("ebusBrokerHost", host),
            )

            # Step 2: Fetch CA certificate for TLS trust
            async with session.get(ca_url) as resp:
                resp.raise_for_status()
                ca_pem = await resp.read()

    except ScrapeError:
        raise
    except aiohttp.ClientError as exc:
        raise ScrapeError("registering", f"Panel unreachable: {exc}") from exc

    _LOGGER.info(
        "Registered with panel %s (serial=%s, mqtts_port=%d)",
        host,
        creds.serial_number,
        creds.mqtts_port,
    )
    return creds, ca_pem


async def scrape_ebus(
    creds: PanelCredentials,
    ca_pem: bytes,
    *,
    status_callback: StatusCallback | None = None,
    stability_timeout: float = _STABILITY_TIMEOUT_S,
    max_timeout: float = _MAX_SCRAPE_TIMEOUT_S,
) -> ScrapedPanel:
    """Connect to a panel's MQTTS broker and collect all retained eBus topics.

    Args:
        creds: MQTT credentials from ``register_with_panel``.
        ca_pem: PEM-encoded CA certificate for the panel's broker.
        status_callback: Optional async callback for progress updates.
        stability_timeout: Seconds of silence before declaring scrape complete.
        max_timeout: Maximum total scrape duration.

    Returns:
        ScrapedPanel with the collected data.

    Raises:
        ScrapeError: On connection failure or missing required topics.
    """
    # The SDK's TLS config takes a CA path, so the PEM needs a file on disk.
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as ca_file:
        ca_file.write(ca_pem)
    ca_path = Path(ca_file.name)
    try:
        devices = await _discover_tree(
            creds,
            ca_path,
            stability_timeout=stability_timeout,
            max_timeout=max_timeout,
            status_callback=status_callback,
        )
    finally:
        ca_path.unlink(missing_ok=True)

    _validate_discovered_tree(devices, creds.serial_number)

    _LOGGER.info(
        "Scrape complete: %d devices discovered for panel %s",
        len(devices),
        creds.serial_number,
    )

    return ScrapedPanel(
        serial_number=creds.serial_number,
        devices=devices,
        mqtts_port=creds.mqtts_port,
        ca_pem=ca_pem,
    )


async def _discover_tree(
    creds: PanelCredentials,
    ca_cert_path: Path,
    *,
    stability_timeout: float,
    max_timeout: float,
    status_callback: StatusCallback | None,
) -> dict[str, DiscoveredDevice]:
    """Discover the panel's whole device tree via a tree-rooted SDK Controller.

    A flat panel published everything under ``ebus/5/<serial>/#``, so a single
    wildcard subscription collected the lot. Under parent/child each entity is its
    own Homie device in its own namespace — circuits, BESS, PV, EVSE, lugs and the
    MID are SIBLINGS of the panel on the wire, not children of its topic prefix. A
    subscription to ``ebus/5/<serial>/#`` would therefore return the panel and
    silently miss every other device in its tree.

    Which devices belong to a given panel is not knowable from topic shape at all;
    it is stated in each device's ``$description`` via ``root`` / ``parent``. That is
    precisely what `Controller`'s tree-rooted mode resolves, so this defers to it
    rather than re-implementing discovery and the membership rule here.
    """
    controller = ebus_sdk.Controller(
        mqtt_cfg={
            "host": creds.broker_host,
            "port": creds.mqtts_port,
            "use_tls": True,
            "tls_ca_cert": str(ca_cert_path),
            "tls_insecure": False,
            "authentication": {
                "type": "USER_PASS",
                "username": creds.username,
                "password": creds.password,
            },
        },
        root_device_id=creds.serial_number,
    )

    if status_callback:
        await status_callback("connecting", f"MQTTS to {creds.broker_host}:{creds.mqtts_port}")

    try:
        controller.start_discovery()

        if status_callback:
            await status_callback(
                "scraping",
                f"Discovering the device tree rooted at {creds.serial_number}",
            )

        # Same stabilisation rule as before: stop once the tree stops growing, with a
        # hard ceiling. Retained state arrives in a burst, so "no new device and no new
        # property for `stability_timeout`" is a better signal than any fixed wait.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_timeout
        last_change = loop.time()
        fingerprint = _tree_fingerprint(controller.devices)

        while loop.time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)
            current = _tree_fingerprint(controller.devices)
            if current != fingerprint:
                fingerprint = current
                last_change = loop.time()
            elif loop.time() - last_change >= stability_timeout:
                break

        devices = dict(controller.devices)
    finally:
        controller.stop()

    return devices


def _tree_fingerprint(devices: Mapping[str, DiscoveredDevice]) -> tuple[tuple[str, int], ...]:
    """A cheap "has anything arrived?" summary: each device and its property count.

    Compared between polls to decide whether the retained burst has finished. Counting
    properties rather than just devices matters — the tree's shape settles before its
    values do, so device ids alone would call it done too early.
    """
    out: list[tuple[str, int]] = []
    for device_id, device in sorted(devices.items()):
        count = sum(len(device.get_node_properties(node)) for node in device.get_nodes())
        out.append((device_id, count))
    return tuple(out)


def _validate_discovered_tree(
    devices: Mapping[str, DiscoveredDevice],
    serial: str,
) -> None:
    """Ensure discovery produced a usable tree before anything tries to clone it.

    Checks the tree rather than topic strings: the root is present, and it has at
    least one circuit child. A panel that answered but whose children never arrived
    is the failure this catches — it looks like success to a topic count.
    """
    root = devices.get(serial)
    if root is None:
        raise ScrapeError("scraping", f"No device published a $description for root {serial}")

    circuits = [
        device_id
        for device_id, device in devices.items()
        if device.root_id == serial and _is_circuit(device)
    ]
    if not circuits:
        raise ScrapeError(
            "scraping",
            "Discovered the panel but none of its circuits. Under the parent/child "
            "model circuits are separate devices, so this usually means discovery "
            "stopped before their retained state arrived.",
        )

    _LOGGER.debug(
        "Validation passed: %d devices, %d circuits for %s",
        len(devices),
        len(circuits),
        serial,
    )


def _is_circuit(device: DiscoveredDevice) -> bool:
    """True when a discovered device is a circuit, by its Homie ``$type``.

    ``description`` is the parsed ``$description`` dict, not a method — a device
    that has not yet published one leaves it empty rather than absent.
    """
    description = device.description
    return bool(isinstance(description, dict) and description.get("type") == TYPE_CIRCUIT)
