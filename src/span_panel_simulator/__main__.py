"""Entry point for the SPAN panel eBus simulator."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Protocol

from span_panel_simulator.app import SimulatorApp
from span_panel_simulator.const import (
    DASHBOARD_PORT,
    DEFAULT_BASE_HTTP_PORT,
    DEFAULT_BROKER_PASSWORD,
    DEFAULT_BROKER_USERNAME,
    DEFAULT_TICK_INTERVAL_S,
    MQTTS_PORT,
)


class _NoisyDependencyFilter(logging.Filter):
    """Drop sub-threshold records from dependencies that log at the wrong level.

    ``aiohttp.access`` logs a line per HTTP request.

    ``homie`` is kept as a guard rather than a live need. Through ebus-sdk 0.17.0 that
    logger pinned *itself* to INFO at import time and dumped every node's full property
    table through ``pformat()`` — thousands of lines per panel while the device graph was
    built, emitted no matter what ``--log-level`` the operator asked for. Verified fixed
    upstream at 0.18.0: ``setLevel`` appears nowhere in the SDK, ``pformat`` is gone from
    ``homie.py``, and the logger is NOTSET at import so it inherits the root level like
    any other. The entry stays because the behaviour has regressed before and one
    ``startswith`` on already-sub-threshold records costs nothing; re-check it on a bump
    before assuming it is still dead weight.

    This filters on the root handler rather than calling ``setLevel()`` on those loggers,
    so it holds regardless of when a module is imported or whether it re-pins its own
    level afterwards.
    """

    NOISY = ("homie", "aiohttp.access")

    def __init__(self, threshold: int) -> None:
        super().__init__()
        self._threshold = threshold

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= self._threshold:
            return True
        return not record.name.startswith(self.NOISY)


class _StoppableApp(Protocol):
    """The slice of `SimulatorApp` the signal wiring needs."""

    async def run(self) -> None: ...

    async def stop(self) -> None: ...


async def _run_until_signalled(app: _StoppableApp) -> None:
    """Run the simulator, shutting it down gracefully on SIGTERM/SIGINT.

    SIGTERM is how `scripts/run-local.sh --stop`, Docker, and the Home Assistant
    supervisor stop the simulator. Python's default disposition for it kills the
    process outright, so `SimulatorApp.run`'s cleanup never ran — and that cleanup
    is what stops each panel, which is what clears the panel's retained MQTT
    topics. Every SIGTERM stop therefore left a full retained tree on the broker
    with `$state` flipped to lost by the Last Will, orphaned for good if that
    panel never came back.

    Routing both signals through `app.stop()` gives every stop path the same
    deterministic shutdown that Ctrl-C previously got only by way of
    `KeyboardInterrupt` unwinding the stack."""
    loop = asyncio.get_running_loop()

    # The stop task is held until it finishes: a bare create_task() reference can
    # be garbage-collected mid-flight, which would drop the shutdown on the floor.
    stop_tasks: set[asyncio.Task[None]] = set()

    def request_stop(signal_name: str) -> None:
        logging.info("%s received — shutting down", signal_name)
        task = loop.create_task(app.stop())
        stop_tasks.add(task)
        task.add_done_callback(stop_tasks.discard)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request_stop, sig.name)

    await app.run()


def _configure_logging(log_level: str) -> None:
    """Set up root logging and hold back the chatty dependencies.

    The noisy loggers are silenced unless the operator explicitly asked for DEBUG
    (``--log-level DEBUG``, or ``scripts/run-local.sh --debug``), which still
    shows everything."""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    threshold = logging.DEBUG if log_level == "DEBUG" else logging.WARNING
    for handler in logging.getLogger().handlers:
        handler.addFilter(_NoisyDependencyFilter(threshold))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="span-simulator",
        description="Standalone eBus simulator for SPAN panels",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path(os.environ.get("CONFIG_DIR", "configs")),
        help="Directory containing YAML simulation configs (one per panel)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_NAME"),
        help="Name of a specific config file to load (e.g., default_config.yaml). "
        "When omitted, loads default_config.yaml if it exists, otherwise all configs.",
    )
    parser.add_argument(
        "--tick-interval",
        type=float,
        default=float(os.environ.get("TICK_INTERVAL", str(DEFAULT_TICK_INTERVAL_S))),
        help="Seconds between simulation ticks",
    )
    parser.add_argument(
        "--broker-host",
        default=os.environ.get("BROKER_HOST", "localhost"),
        help="MQTT broker hostname",
    )
    parser.add_argument(
        "--broker-port",
        type=int,
        default=int(os.environ.get("BROKER_PORT", str(MQTTS_PORT))),
        help="MQTT broker port",
    )
    parser.add_argument(
        "--base-http-port",
        type=int,
        default=int(os.environ.get("HTTP_PORT", str(DEFAULT_BASE_HTTP_PORT))),
        help="Base port for per-panel bootstrap HTTP servers. "
        "First panel uses this port, second uses port+1, etc.",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=None,
        help="Deprecated: use --base-http-port instead",
    )
    parser.add_argument(
        "--broker-username",
        default=os.environ.get("BROKER_USERNAME", DEFAULT_BROKER_USERNAME),
    )
    parser.add_argument(
        "--broker-password",
        default=os.environ.get("BROKER_PASSWORD", DEFAULT_BROKER_PASSWORD),
    )
    parser.add_argument(
        "--cert-dir",
        type=Path,
        default=Path(os.environ.get("CERT_DIR", "/tmp/span-sim-certs")),
        help="Directory for generated TLS certificates",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=int(os.environ.get("DASHBOARD_PORT", str(DASHBOARD_PORT))),
        help="Port for the configuration dashboard (default: 8080)",
    )
    parser.add_argument(
        "--advertise-address",
        default=os.environ.get("ADVERTISE_ADDRESS"),
        help="IP address to advertise via mDNS (required when running in a VM)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    # Home Assistant API — for local development.  When running as an
    # add-on, SUPERVISOR_TOKEN is injected automatically and these are
    # not needed.
    parser.add_argument(
        "--ha-url",
        default=os.environ.get("HA_URL"),
        help="Home Assistant URL (e.g. http://192.168.1.10:8123). "
        "Not needed when running as an HA add-on.",
    )
    parser.add_argument(
        "--ha-token",
        default=os.environ.get("HA_TOKEN"),
        help="Long-lived access token for Home Assistant. "
        "Not needed when running as an HA add-on.",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    args = _parse_args(argv)

    # Resolve deprecated --http-port alias
    base_http_port = args.base_http_port
    if args.http_port is not None:
        logging.warning("--http-port is deprecated, use --base-http-port instead")
        base_http_port = args.http_port

    _configure_logging(args.log_level)

    config_dir: Path = args.config_dir
    if not config_dir.is_dir():
        logging.error("Config directory not found: %s", config_dir)
        sys.exit(1)

    # Resolve which config(s) to load.
    # When --config is given explicitly, that panel auto-starts.
    # Otherwise, resume the last-used config if saved.
    # If nothing to resume, start idle (empty filter) so the dashboard
    # is ready for the user to choose a config.
    config_filter: str | None = args.config
    if config_filter:
        config_path = config_dir / config_filter
        if not config_path.exists():
            logging.error("Config file not found: %s", config_path)
            sys.exit(1)
        logging.info("Using config: %s", config_path.name)
    else:
        last_config_file = config_dir / ".last_config"
        if last_config_file.exists():
            last_name = last_config_file.read_text(encoding="utf-8").strip()
            if last_name and (config_dir / last_name).exists():
                config_filter = last_name
                logging.info("Resuming last config: %s", last_name)

        if config_filter is None:
            config_filter = ""  # idle — no panel until user picks one
            logging.info("No config to resume — dashboard ready, no panel running")

    # Resolve HA API connection (add-on mode auto-detects via env var)
    from span_panel_simulator.ha_api.client import HAConnectionConfig

    ha_config = HAConnectionConfig.from_environment(
        ha_url=args.ha_url,
        ha_token=args.ha_token,
    )
    if ha_config:
        logging.info("HA API configured: %s", ha_config.base_url)
    else:
        logging.info("HA API not configured — running without HA integration")

    app = SimulatorApp(
        config_dir=config_dir,
        config_filter=config_filter,
        tick_interval=args.tick_interval,
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        base_http_port=base_http_port,
        broker_username=args.broker_username,
        broker_password=args.broker_password,
        cert_dir=args.cert_dir,
        dashboard_port=args.dashboard_port,
        advertise_address=args.advertise_address,
        ha_config=ha_config,
    )

    try:
        asyncio.run(_run_until_signalled(app))
    except KeyboardInterrupt:
        logging.info("Interrupted — shutting down")


if __name__ == "__main__":
    main()
