"""eBus scraper — connect to a real SPAN panel and discover its device tree.

Performs the authentication handshake via the panel's v2 REST API, then drives a
tree-rooted ``ebus_sdk.Controller`` against the panel's MQTTS broker until every
device the panel declares has described itself and the retained burst settles.

**The broker is dialled at the host the user gave.** A panel advertises its own
mDNS name (``span-….local``) as its broker host, and mDNS does not resolve across
subnets, so a scrape that dialled it never connected. The broker runs on the panel
itself, so the host the panel was reached at is the broker's, and TLS is verified
against the panel's CA, whose leaf names that address, exactly as the Home
Assistant integration connects.

Discovery is delegated rather than hand-rolled because under the parent/child data
model a panel's circuits, BESS, PV, EVSE, lugs and MID are SEPARATE Homie devices in
sibling namespaces. Tree membership is declared in each device's ``$description``
(``root`` / ``parent``), not implied by topic prefix, so a wildcard subscription
cannot express "this panel's devices" — see ``_discover_tree``.

This module still imports no span-panel-api or HA integration code. It uses
``aiohttp`` for the REST handshake and ``ebus_sdk`` for discovery.

**A panel is registered with once.** Each registration adds a client to the real
panel that its owner has to remove by hand, so ``scrape_panel`` registers under a
name derived from the panel's serial, keeps the credentials registering returns in
the ``PanelSecretsStore``, and reuses them on every later clone, sync and restore.
It registers again, under the same name, only when the broker refuses them; and a
user who already holds broker credentials can supply them instead of registering.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import aiomqtt
import ebus_sdk
from aiomqtt.exceptions import MqttConnectError
from ebus_sdk import DiscoveredDevice
from paho.mqtt.reasoncodes import ReasonCode

from panelbench.const import PATH_CA_CERT, PATH_REGISTER, PATH_STATUS
from panelbench.emitter_adapter.broker_link import BrokerLink
from panelbench.emitter_adapter.transport import LoopBoundTransport
from panelbench.panel_secrets import BrokerCredentials, PanelSecretsStore, PanelSecretsUnreadable

_LOGGER = logging.getLogger(__name__)

# Timeouts
_CONNECT_TIMEOUT_S = 15.0
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


class BrokerRefused(ScrapeError):
    """The panel's broker refused the credentials: bad, or no longer authorised.

    The one scrape failure a new registration can fix, so the only one that leads to
    one. A broker that does not answer, a tree that never completes, a panel with no
    circuits: registering again cannot help any of them, and each registration adds a
    client the panel's owner removes by hand.
    """

    def __init__(self, message: str) -> None:
        super().__init__("connecting", message)


# CONNACK codes that refuse the credentials: 4 and 5 under MQTT 3.1.1, which paho
# reports as their MQTT 5 equivalents, 0x86 (bad user name or password) and 0x87
# (not authorised).
_REFUSED_CONNACK_CODES = frozenset({4, 5, 0x86, 0x87})


@dataclass(frozen=True, slots=True)
class PanelCredentials:
    """MQTT credentials and identity returned by the panel's /register endpoint."""

    username: str
    password: str
    serial_number: str
    mqtts_port: int
    broker_host: str


@dataclass(frozen=True, slots=True)
class SuppliedBroker:
    """Broker credentials a user already holds for a panel, so nothing is registered.

    ``ca_pem`` may be left out, and is then fetched from the panel, which serves its
    CA without authentication.
    """

    serial: str
    username: str
    password: str
    port: int
    ca_pem: str | None = None


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


def registered_client_name(serial: str) -> str:
    """The name PanelBench registers under with the panel whose serial is *serial*.

    Stable, so the panel's owner sees one PanelBench client per panel, and can tell
    which panel's clone it serves.
    """
    return f"panelbench-clone-{serial}"


async def scrape_panel(
    host: str,
    secrets: PanelSecretsStore,
    *,
    passphrase: str | None = None,
    supplied: SuppliedBroker | None = None,
    status_callback: StatusCallback | None = None,
) -> ScrapedPanel:
    """Scrape the panel at *host*, registering with it only when nothing else will do.

    With *supplied*, those credentials are kept and used, and nothing is registered.
    Otherwise the panel's serial is read from its status endpoint, and the broker
    credentials kept for it are used. Without any, or when its broker refuses
    them, PanelBench registers under ``registered_client_name`` with *passphrase*, or
    the one kept for the panel, and keeps what registering returns.

    Raises:
        ScrapeError: The panel could not be reached, registered with or scraped, or
            the secrets store cannot be read.
    """
    try:
        return await _scrape_panel(host, secrets, passphrase, supplied, status_callback)
    except PanelSecretsUnreadable as exc:
        raise ScrapeError("secrets", str(exc)) from exc


async def _scrape_panel(
    host: str,
    secrets: PanelSecretsStore,
    passphrase: str | None,
    supplied: SuppliedBroker | None,
    status_callback: StatusCallback | None,
) -> ScrapedPanel:
    serial = await _fetch_serial(host)
    if supplied is not None:
        if supplied.serial != serial:
            # Kept under the wrong serial, they would serve another panel's clone.
            raise ScrapeError(
                "registering",
                f"The panel at {host} reports serial {serial}, not {supplied.serial}",
            )
        broker = BrokerCredentials(
            username=supplied.username,
            password=supplied.password,
            port=supplied.port,
            ca_pem=supplied.ca_pem or (await _fetch_ca(host)).decode(),
        )
        scraped = await _scrape_through(host, serial, broker, status_callback)
        # Kept only once they work, so a mistyped password replaces nothing that did.
        secrets.remember(serial, passphrase=passphrase, broker=broker)
        return scraped

    kept = secrets.get(serial)
    passphrase = passphrase or kept.passphrase
    if kept.broker is not None:
        try:
            return await _scrape_through(host, serial, kept.broker, status_callback)
        except BrokerRefused as exc:
            if passphrase is None:
                raise
            _LOGGER.warning(
                "The broker refused the credentials kept for panel %s (%s); registering again",
                serial,
                exc,
            )

    creds, ca_pem = await register_with_panel(host, passphrase, serial=serial)
    broker = BrokerCredentials(
        username=creds.username,
        password=creds.password,
        port=creds.mqtts_port,
        ca_pem=ca_pem.decode(),
    )
    secrets.remember(serial, passphrase=passphrase, broker=broker)
    return await _scrape_through(host, serial, broker, status_callback)


async def _scrape_through(
    host: str,
    serial: str,
    broker: BrokerCredentials,
    status_callback: StatusCallback | None,
) -> ScrapedPanel:
    """Scrape *serial*'s tree from the broker on the panel at *host*."""
    creds = PanelCredentials(
        username=broker.username,
        password=broker.password,
        serial_number=serial,
        mqtts_port=broker.port,
        broker_host=_hostname(host),
    )
    return await scrape_ebus(creds, broker.ca_pem.encode(), status_callback=status_callback)


def _hostname(host: str) -> str:
    """*host* without a port, which is where the panel's broker listens too."""
    return urlsplit(f"//{host}").hostname or host


def _http_timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_S)


async def _fetch_serial(host: str) -> str:
    """The panel's serial, from the status endpoint it serves without authentication."""
    try:
        async with (
            aiohttp.ClientSession(timeout=_http_timeout()) as session,
            session.get(f"http://{host}{PATH_STATUS}") as resp,
        ):
            resp.raise_for_status()
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ScrapeError("registering", f"Panel unreachable: {exc}") from exc
    serial = data.get("serialNumber") if isinstance(data, dict) else None
    if not isinstance(serial, str) or not serial:
        raise ScrapeError("registering", "The panel's status reports no serial number")
    return serial


async def _fetch_ca(host: str) -> bytes:
    """The panel's CA certificate, which it serves without authentication."""
    try:
        async with (
            aiohttp.ClientSession(timeout=_http_timeout()) as session,
            session.get(f"http://{host}{PATH_CA_CERT}") as resp,
        ):
            resp.raise_for_status()
            return await resp.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ScrapeError("registering", f"Panel unreachable: {exc}") from exc


async def register_with_panel(
    host: str,
    passphrase: str | None,
    *,
    serial: str,
) -> tuple[PanelCredentials, bytes]:
    """Register with a real SPAN panel, under ``registered_client_name(serial)``.

    Args:
        host: IP or hostname of the panel.
        passphrase: Panel passphrase (None for door-bypass).
        serial: The panel's serial, which names the registration.

    Returns:
        A tuple of (PanelCredentials, ca_pem_bytes).

    Raises:
        ScrapeError: On network or authentication failure.
    """
    register_url = f"http://{host}{PATH_REGISTER}"
    ca_url = f"http://{host}{PATH_CA_CERT}"

    body: dict[str, str] = {"name": registered_client_name(serial)}
    if passphrase is not None:
        body["hopPassphrase"] = passphrase

    try:
        async with aiohttp.ClientSession(timeout=_http_timeout()) as session:
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
                # Not `ebusBrokerHost`: that is the panel's .local name, which does
                # not resolve across subnets. See the module docstring.
                broker_host=_hostname(host),
            )

            # Step 2: Fetch CA certificate for TLS trust
            async with session.get(ca_url) as resp:
                resp.raise_for_status()
                ca_pem = await resp.read()

    except ScrapeError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ScrapeError("registering", f"Panel unreachable: {exc}") from exc

    _LOGGER.info(
        "Registered with panel %s as %s (serial=%s, mqtts_port=%d)",
        host,
        body["name"],
        creds.serial_number,
        creds.mqtts_port,
    )
    return creds, ca_pem


async def scrape_ebus(
    creds: PanelCredentials,
    ca_pem: bytes,
    *,
    status_callback: StatusCallback | None = None,
    connect_timeout: float = _CONNECT_TIMEOUT_S,
    stability_timeout: float = _STABILITY_TIMEOUT_S,
    max_timeout: float = _MAX_SCRAPE_TIMEOUT_S,
) -> ScrapedPanel:
    """Connect to a panel's MQTTS broker and collect its whole device tree.

    Args:
        creds: MQTT credentials, and the host the panel's broker is reached at.
        ca_pem: PEM-encoded CA certificate for the panel's broker.
        status_callback: Optional async callback for progress updates.
        connect_timeout: How long the broker has to accept the connection.
        stability_timeout: Seconds of quiet, once the tree is whole, before the
            scrape takes it.
        max_timeout: How long the tree has to arrive once connected.

    Returns:
        ScrapedPanel with the collected data.

    Raises:
        ScrapeError: The broker could not be reached, or the tree did not arrive.
    """
    # aiomqtt's TLS parameters take a CA path, so the PEM needs a file on disk.
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as ca_file:
        ca_file.write(ca_pem)
    ca_path = Path(ca_file.name)
    try:
        devices = await _discover_tree(
            host=creds.broker_host,
            port=creds.mqtts_port,
            root=creds.serial_number,
            username=creds.username,
            password=creds.password,
            ca_cert_path=ca_path,
            connect_timeout=connect_timeout,
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
    *,
    host: str,
    port: int,
    root: str,
    username: str | None,
    password: str | None,
    ca_cert_path: Path | None,
    connect_timeout: float,
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

    The controller runs on the same broker link and transport a simulated panel
    publishes through, so connecting is an awaitable step with its own timeout, and
    a broker that never answers is reported as that, naming it. Once connected, the
    scrape waits for ``Controller.is_tree_complete`` — every device the panel
    declares has described itself — and then for *stability_timeout* of quiet so
    the retained values land. The quiet period counts only from the first message,
    so a panel slow to start answering is not taken for an empty one.
    """
    address = f"{host}:{port}"
    link = BrokerLink(
        host=host,
        port=port,
        # A scrape is a passing reader, so any id that collides with no one will do.
        client_id=f"panelbench-scrape-{uuid.uuid4().hex[:12]}",
        username=username,
        password=password,
        ca_cert_path=str(ca_cert_path) if ca_cert_path is not None else None,
    )

    if status_callback:
        await status_callback("connecting", f"MQTTS to {address}")
    await _connect(link, address, connect_timeout)

    # Everything after the connect is inside the try, and the link's disconnect is
    # in a finally of its own: a link that outlives the scrape reconnects for the
    # life of the process with the source panel's credentials.
    transport: LoopBoundTransport | None = None
    controller: ebus_sdk.Controller | None = None
    try:
        transport = LoopBoundTransport(
            publish=link.publish,
            subscribe=link.subscribe,
            unsubscribe=link.unsubscribe,
            connected=link.is_connected,
        )
        transport.start()
        controller = ebus_sdk.Controller(mqtt_cfg=None, root_device_id=root, mqttc=transport)
        # The SDK resyncs a tree-rooted controller on reconnect only for a client it
        # built; for one it is given, that is the caller's to wire.
        link.on_reconnect(controller.resync)
        controller.start_discovery()
        if status_callback:
            await status_callback("scraping", f"Discovering the device tree rooted at {root}")
        await _await_whole_tree(
            controller, root, stability_timeout=stability_timeout, max_timeout=max_timeout
        )
        return dict(controller.devices)
    finally:
        try:
            if controller is not None:
                controller.stop()
            if transport is not None:
                await transport.aclose()
        finally:
            await link.disconnect()


async def _connect(link: BrokerLink, address: str, timeout: float) -> None:
    """Open *link*, or say why not: refused credentials apart from every other failure."""
    try:
        async with asyncio.timeout(timeout):
            await link.connect()
    except TimeoutError as exc:
        raise ScrapeError(
            "connecting", f"The panel's broker at {address} did not answer within {timeout:g} s"
        ) from exc
    except MqttConnectError as exc:
        if _connack_code(exc) in _REFUSED_CONNACK_CODES:
            raise BrokerRefused(
                f"The panel's broker at {address} refused the credentials: {exc}"
            ) from exc
        raise ScrapeError(
            "connecting", f"Could not connect to the panel's broker at {address}: {exc}"
        ) from exc
    except aiomqtt.MqttError as exc:
        raise ScrapeError(
            "connecting", f"Could not connect to the panel's broker at {address}: {exc}"
        ) from exc


def _connack_code(exc: MqttConnectError) -> int | None:
    rc = exc.rc
    return rc.value if isinstance(rc, ReasonCode) else rc


async def _await_whole_tree(
    controller: ebus_sdk.Controller,
    root: str,
    *,
    stability_timeout: float,
    max_timeout: float,
) -> None:
    """Wait until *root*'s tree is whole and quiet, or until *max_timeout*.

    What arrived by the deadline is taken as it stands; validating it says what is
    missing, which the wait alone cannot.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_timeout
    fingerprint = _tree_fingerprint(controller.devices)
    last_change: float | None = None
    while loop.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_S)
        current = _tree_fingerprint(controller.devices)
        if current != fingerprint:
            fingerprint = current
            last_change = loop.time()
            continue
        if (
            last_change is not None
            and loop.time() - last_change >= stability_timeout
            and controller.is_tree_complete(root)
        ):
            return


def _tree_fingerprint(
    devices: Mapping[str, DiscoveredDevice],
) -> tuple[tuple[str, bool, int], ...]:
    """A cheap "has anything arrived?" summary: each device, whether it has described
    itself, and how many values it has published.

    Compared between polls to decide whether the retained burst has finished. The
    values count, not only the shape: a tree's descriptions settle before its values.
    """
    return tuple(
        (
            device_id,
            device.description is not None,
            sum(len(values) for values in device.properties.values()),
        )
        for device_id, device in sorted(devices.items())
    )


def _validate_discovered_tree(
    devices: Mapping[str, DiscoveredDevice],
    serial: str,
) -> None:
    """Ensure discovery produced a usable tree before anything tries to clone it.

    Checks the tree rather than topic strings: the root described itself, every
    device it declares did too, and it has at least one circuit child. The SDK
    creates the root's entry before anything arrives, so the root being present
    says nothing; its ``$description`` is what says it answered.
    """
    root = devices.get(serial)
    if root is None or root.description is None:
        raise ScrapeError("scraping", f"The panel published no $description for its root {serial}")

    undescribed = [
        child
        for child in _declared_descendants(devices, serial)
        if (device := devices.get(child)) is None or device.description is None
    ]
    if undescribed:
        raise ScrapeError(
            "scraping",
            f"{len(undescribed)} devices the panel declares never described themselves "
            f"({', '.join(sorted(undescribed)[:5])}{', …' if len(undescribed) > 5 else ''})",
        )

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


def _declared_descendants(devices: Mapping[str, DiscoveredDevice], root: str) -> list[str]:
    """Every device id *root*'s tree declares, by ``children``, cycles tolerated."""
    seen = {root}
    queue = list(devices[root].children_ids) if root in devices else []
    out: list[str] = []
    while queue:
        child = queue.pop(0)
        if child in seen:
            continue
        seen.add(child)
        out.append(child)
        device = devices.get(child)
        if device is not None:
            queue.extend(device.children_ids)
    return out


def _is_circuit(device: DiscoveredDevice) -> bool:
    """True when a discovered device is a circuit, by its Homie ``$type``.

    ``description`` is the parsed ``$description`` dict, not a method — a device
    that has not yet published one leaves it empty rather than absent.
    """
    description = device.description
    return bool(isinstance(description, dict) and description.get("type") == TYPE_CIRCUIT)
