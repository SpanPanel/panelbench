"""Tests for SimulatorApp multi-panel config discovery and reload."""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from panelbench.app import SimulatorApp, _discover_configs, _file_hash
from panelbench.bootstrap import BootstrapHttpServer
from panelbench.const import DEFAULT_FIRMWARE_VERSION
from panelbench.emitter_adapter.runtime import start_clone as _real_start_clone
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.panel import PanelInstance
from panelbench.schema import load_schema
from tests._helpers import CURRENT_FIRMWARE, EARLIER_FIRMWARE

_SIMPLE_CONFIG = """\
panel_config:
  serial_number: "{serial}"
  total_tabs: 8
  main_size: 100

broker:
  host: "{broker_host}"
  port: {broker_port}

circuit_templates:
  lighting:
    energy_profile:
      mode: "consumer"
      power_range: [5.0, 50.0]
      typical_power: 25.0
      power_variation: 0.1
    relay_behavior: "controllable"
    priority: "NEVER"

circuits:
  - id: "test_circuit"
    name: "Test Circuit"
    template: "lighting"
    tabs: [1]

unmapped_tabs: []

simulation_params:
  update_interval: 5
  time_acceleration: 1.0
  noise_factor: 0.0
  enable_realistic_behaviors: false
"""


def _write_config(
    config_dir: Path,
    name: str,
    serial: str,
    *,
    broker_host: str = "127.0.0.1",
    broker_port: int = 1,
) -> Path:
    path = config_dir / name
    path.write_text(
        _SIMPLE_CONFIG.format(serial=serial, broker_host=broker_host, broker_port=broker_port),
    )
    return path


class TestConfigDiscovery:
    """Config directory scanning."""

    def test_finds_yaml_files(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "a.yaml", "PANEL-A")
        _write_config(tmp_path, "b.yml", "PANEL-B")
        (tmp_path / "not_config.txt").write_text("ignored")

        configs = _discover_configs(tmp_path)
        assert len(configs) == 2
        names = {p.name for p in configs}
        assert names == {"a.yaml", "b.yml"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        configs = _discover_configs(tmp_path)
        assert len(configs) == 0

    def test_hash_changes_on_modification(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, "panel.yaml", "PANEL-A")
        hash1 = _file_hash(path)

        _write_config(tmp_path, "panel.yaml", "PANEL-B")
        hash2 = _file_hash(path)

        assert hash1 != hash2

    def test_hash_stable_for_same_content(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, "panel.yaml", "PANEL-A")
        assert _file_hash(path) == _file_hash(path)


_BUNDLED_SCHEMA = (
    Path(__file__).parent.parent / "src" / "panelbench" / "data" / "homie_schema.json"
)

_BAD_SIZE_CONFIG = """\
panel_config:
  serial_number: "SIM-BAD-20"
  total_tabs: 20
  main_size: 100

broker:
  host: "{broker_host}"
  port: {broker_port}

circuit_templates:
  lighting:
    energy_profile:
      mode: "consumer"
      power_range: [5.0, 50.0]
      typical_power: 25.0
      power_variation: 0.1
    relay_behavior: "controllable"
    priority: "NEVER"

circuits:
  - id: "c1"
    name: "C1"
    template: "lighting"
    tabs: [1]

unmapped_tabs: []

simulation_params:
  update_interval: 5
  time_acceleration: 1.0
  noise_factor: 0.0
  enable_realistic_behaviors: false
"""


class TestUnsupportedPanelSize:
    """Configs with total_tabs outside _PANEL_SIZE_TO_MODEL fail loudly at panel-add."""

    @pytest.mark.asyncio
    async def test_unsupported_total_tabs_raises_key_error(
        self,
        tmp_path: Path,
        amqtt_broker: tuple[str, int],
    ) -> None:
        """total_tabs=20 has no SPAN model — _start_panel must raise AND not register."""
        host, port = amqtt_broker
        config = tmp_path / "bad_size.yaml"
        config.write_text(_BAD_SIZE_CONFIG.format(broker_host=host, broker_port=port))

        app = SimulatorApp(config_dir=tmp_path)
        app._schema = load_schema(_BUNDLED_SCHEMA)
        # ca_cert_path=None makes the per-clone runtime skip TLS — these tests
        # use the plain-TCP amqtt_broker fixture, not a TLS broker.
        app._certs = MagicMock(
            ca_cert_pem=b"-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n",
            ca_cert_path=None,
        )

        with pytest.raises(KeyError):
            await app._start_panel(config)

        assert config not in app._panels
        assert not app._serial_to_panel


class TestReloadContinuesOnPerPathFailure:
    """A failing config does not block other panels in the same reload."""

    @pytest.mark.asyncio
    async def test_good_panel_starts_despite_bad_peer(
        self,
        tmp_path: Path,
        amqtt_broker: tuple[str, int],
    ) -> None:
        """Unsupported total_tabs in one config must not prevent starting another."""
        host, port = amqtt_broker
        # total_tabs=32 is a valid SPAN panel size; total_tabs=20 is not.
        good_config = _SIMPLE_CONFIG.replace("total_tabs: 8", "total_tabs: 32")
        good = tmp_path / "good.yaml"
        good.write_text(good_config.format(serial="SIM-GOOD", broker_host=host, broker_port=port))
        bad = tmp_path / "bad.yaml"
        bad.write_text(_BAD_SIZE_CONFIG.format(broker_host=host, broker_port=port))

        app = SimulatorApp(config_dir=tmp_path)
        app._schema = load_schema(_BUNDLED_SCHEMA)
        # ca_cert_path=None makes the per-clone runtime skip TLS — these tests
        # use the plain-TCP amqtt_broker fixture, not a TLS broker.
        app._certs = MagicMock(
            ca_cert_pem=b"-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n",
            ca_cert_path=None,
        )

        mock_server = MagicMock()
        mock_server.start = AsyncMock()
        mock_server.stop = AsyncMock()

        with patch("panelbench.app.BootstrapHttpServer", return_value=mock_server):
            result = await app.reload()

        # Good panel started; bad one recorded as an error.
        assert "SIM-GOOD" in result["started"]
        assert result["errors"], "expected at least one error entry"
        assert any("bad.yaml" in msg for msg in result["errors"])

        # Error surfaced via the per-filename dict.
        errors = app._get_panel_start_errors()
        assert "bad.yaml" in errors
        assert "good.yaml" not in errors

        # Good panel is registered; bad panel is not.
        assert good in app._panels
        assert bad not in app._panels

        # Bad panel's hash is NOT recorded, so next reload retries it.
        assert good in app._config_hashes
        assert bad not in app._config_hashes

        # Cleanup
        for panel in list(app._panels.values()):
            with contextlib.suppress(Exception):
                await panel.stop()


async def _started(
    tmp_path: Path, broker: tuple[str, int], extra_yaml: str
) -> tuple[PanelInstance, MagicMock, MagicMock]:
    """One panel started through `_start_panel`, its HTTP server class and mDNS
    advertiser replaced by mocks that record what `_start_panel` hands them."""
    host, port = broker
    config = _write_config(
        tmp_path, "panel.yaml", "SIM-FW-0001", broker_host=host, broker_port=port
    )
    config.write_text(config.read_text() + extra_yaml)

    app = SimulatorApp(config_dir=tmp_path)
    app._schema = load_schema(_BUNDLED_SCHEMA)
    app._certs = MagicMock(
        ca_cert_pem=b"-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n",
        ca_cert_path=None,
    )
    advertiser = MagicMock()
    advertiser.register_panel = AsyncMock()
    app._advertiser = advertiser
    mock_server = MagicMock()
    mock_server.start = AsyncMock()
    mock_server.stop = AsyncMock()

    with patch("panelbench.app.BootstrapHttpServer", return_value=mock_server) as server_cls:
        panel = await app._start_panel(config)
    return panel, server_cls, advertiser


class TestOneFirmwareString:
    """A panel's HTTP status and mDNS record report the firmware its MQTT tree publishes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("extra_yaml", "firmware"),
        [
            (f'firmware_version: "{EARLIER_FIRMWARE}"\n', EARLIER_FIRMWARE),
            # Before, HTTP and mDNS reported the package version while MQTT
            # published its own default, so a panel naming none saw two strings.
            ("", DEFAULT_FIRMWARE_VERSION),
        ],
        ids=["named", "unnamed"],
    )
    async def test_the_status_endpoint_reports_the_published_firmware(
        self,
        tmp_path: Path,
        amqtt_broker: tuple[str, int],
        extra_yaml: str,
        firmware: str,
    ) -> None:
        panel, server_cls, advertiser = await _started(tmp_path, amqtt_broker, extra_yaml)
        try:
            engine = panel.engine
            assert engine is not None
            [published] = [
                i for i in build_manifest(engine.config).instances if i.entity_class == "panel"
            ]
            served = server_cls.call_args.args[1]
            advertised = advertiser.register_panel.call_args.args[1]

            assert served == advertised == panel.firmware_version
            assert served == published.metadata["firmware-version"] == firmware
        finally:
            await panel.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "extra_yaml",
        [f'firmware_version: "{EARLIER_FIRMWARE}"\n', ""],
        ids=["named", "unnamed"],
    )
    async def test_the_schema_endpoint_reports_the_status_firmware(
        self,
        tmp_path: Path,
        amqtt_broker: tuple[str, int],
        extra_yaml: str,
    ) -> None:
        """A server built from what `_start_panel` hands it serves one firmware string
        on both endpoints, rather than the bundled schema document's own."""
        panel, server_cls, _advertiser = await _started(tmp_path, amqtt_broker, extra_yaml)
        try:
            server = BootstrapHttpServer(*server_cls.call_args.args, **server_cls.call_args.kwargs)
            async with TestClient(TestServer(server._app)) as client:
                status = await (await client.get("/api/v2/status")).json()
                schema = await (await client.get("/api/v2/homie/schema")).json()

            assert schema["firmwareVersion"] == status["firmwareVersion"] == panel.firmware_version
        finally:
            await panel.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("firmware", "reported"),
        [(EARLIER_FIRMWARE, None), (CURRENT_FIRMWARE, "1.2")],
    )
    async def test_the_status_endpoint_reports_hardware_from_202639(
        self,
        tmp_path: Path,
        amqtt_broker: tuple[str, int],
        firmware: str,
        reported: str | None,
    ) -> None:
        extra = f'firmware_version: "{firmware}"\nhardware_version: "1.2"\n'
        panel, server_cls, _advertiser = await _started(tmp_path, amqtt_broker, extra)
        try:
            assert server_cls.call_args.kwargs["hardware_version"] == reported
            assert panel.status_hardware_version == reported
        finally:
            await panel.stop()


class TestDuplicateSerial:
    """Two configs naming one serial would publish over each other's topics and take
    each other's broker client id, forever, so the second is refused."""

    @pytest.mark.asyncio
    async def test_a_second_panel_with_a_running_serial_is_refused(
        self,
        tmp_path: Path,
        amqtt_broker: tuple[str, int],
    ) -> None:
        host, port = amqtt_broker
        first_config = _SIMPLE_CONFIG.replace("total_tabs: 8", "total_tabs: 32")
        first = tmp_path / "a-first.yaml"
        first.write_text(first_config.format(serial="SIM-DUP", broker_host=host, broker_port=port))
        second = tmp_path / "b-second.yaml"
        second.write_text(first.read_text())

        app = SimulatorApp(config_dir=tmp_path)
        app._schema = load_schema(_BUNDLED_SCHEMA)
        app._certs = MagicMock(
            ca_cert_pem=b"-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n",
            ca_cert_path=None,
        )
        mock_server = MagicMock()
        mock_server.start = AsyncMock()
        mock_server.stop = AsyncMock()

        with (
            patch("panelbench.app.BootstrapHttpServer", return_value=mock_server),
            patch("panelbench.panel.emitter_runtime.start_clone") as start_clone,
        ):
            start_clone.side_effect = _real_start_clone
            result = await app.reload()

        try:
            # Which one wins is the scan's order; that exactly one does is the point.
            assert result["started"] == ["SIM-DUP"]
            [(refused, error)] = app._get_panel_start_errors().items()
            [running] = app._panels
            assert {refused, running.name} == {first.name, second.name}
            assert "SIM-DUP" in error and running.name in error
            assert start_clone.call_count == 1, "the duplicate connected before it was refused"
        finally:
            for panel in list(app._panels.values()):
                with contextlib.suppress(Exception):
                    await panel.stop()
