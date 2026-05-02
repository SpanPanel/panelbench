"""PanelInstance — encapsulates a single simulated panel.

Each instance owns a CloneRuntime (assembled by the emitter_adapter) which in turn
owns an Emitter and its per-clone MQTT client. The tick loop calls into the emitter's
``tick()`` to produce the next snapshot and publish the diff."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from span_panel_simulator.emitter_adapter import runtime as emitter_runtime
from span_panel_simulator.emitter_adapter.profile_loader import load_clone_profile

if TYPE_CHECKING:
    from pathlib import Path

    from ebus_emitter import EbusPanelSnapshot

    from span_panel_simulator.emitter_adapter.runtime import CloneRuntime

_LOGGER = logging.getLogger(__name__)


class PanelInstance:
    """A single simulated panel with its own emitter and per-clone MQTT client."""

    def __init__(
        self,
        config_path: Path,
        *,
        tick_interval: float = 1.0,
    ) -> None:
        self._config_path = config_path
        self._tick_interval = tick_interval
        self._runtime: CloneRuntime | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._running = False
        self._last_snapshot: EbusPanelSnapshot | None = None

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def serial_number(self) -> str:
        if self._runtime is None:
            raise RuntimeError("Panel not initialised — call start() first")
        return str(self._runtime.runtime_spec.panel.instance_id)

    @property
    def total_tabs(self) -> int:
        if self._runtime is None:
            raise RuntimeError("Panel not initialised — call start() first")
        return int(self._runtime.clone_profile["panel_config"]["total_tabs"])

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def runtime(self) -> CloneRuntime | None:
        return self._runtime

    @property
    def last_snapshot(self) -> EbusPanelSnapshot | None:
        return self._last_snapshot

    async def start(self) -> str:
        """Load the clone profile, build the emitter via emitter_adapter, run the
        cold-start lifecycle, and begin the tick loop. Returns the panel serial."""
        profile = await load_clone_profile(self._config_path)
        self._runtime = await emitter_runtime.start_clone(profile)
        serial = str(self._runtime.runtime_spec.panel.instance_id)
        self._running = True
        self._tick_task = asyncio.create_task(self._tick_loop(), name=f"tick-{serial}")
        _LOGGER.info("Panel %s started (config=%s)", serial, self._config_path.name)
        return serial

    async def stop(self) -> None:
        self._running = False

        if self._tick_task is not None:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None

        if self._runtime is not None:
            try:
                await emitter_runtime.stop_clone(self._runtime, graceful=True)
            except Exception:
                _LOGGER.debug(
                    "Error during stop_clone for %s",
                    self._runtime.runtime_spec.panel.instance_id,
                )
            serial = self._runtime.runtime_spec.panel.instance_id
            self._runtime = None
            _LOGGER.info("Panel %s stopped", serial)

    async def reload(self) -> str:
        """Stop, re-read configuration, and restart."""
        _LOGGER.info("Reloading panel from %s", self._config_path.name)
        await self.stop()
        return await self.start()

    async def _tick_loop(self) -> None:
        assert self._runtime is not None
        while self._running:
            await asyncio.sleep(self._tick_interval)
            try:
                snapshot = await emitter_runtime.on_tick(self._runtime)
                self._last_snapshot = snapshot
            except Exception:
                _LOGGER.exception(
                    "tick failed for %s",
                    self._runtime.runtime_spec.panel.instance_id,
                )
