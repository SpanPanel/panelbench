"""PanelInstance — wraps a single ``DynamicSimulationEngine`` plus its emitter
runtime. The tick loop calls ``emitter_runtime.publish_tick`` which collects the
engine's per-circuit signed power into ``TickInputs``; the emitter resolves BESS
dispatch, relay state, energy integration, panel meter aggregation, and
publishes the resulting snapshot."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.engine import DynamicSimulationEngine

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from panelbench.config_types import BESSConfigYAML
    from panelbench.ebus_emitter import EbusBatterySnapshot
    from panelbench.emitter_adapter.runtime import BrokerConnection, CloneRuntime
    from panelbench.recorder import RecorderDataSource

_LOGGER = logging.getLogger(__name__)


class PanelInstance:
    """A single simulated panel: engine + emitter, with its own tick loop."""

    def __init__(
        self,
        config_path: Path,
        *,
        tick_interval: float = 1.0,
        recorder: RecorderDataSource | None = None,
        broker: BrokerConnection | None = None,
    ) -> None:
        self._config_path = config_path
        self._tick_interval = tick_interval
        self._recorder = recorder
        # App-level broker connection. Used when the YAML config doesn't carry
        # its own broker section (the typical case — clones share the simulator
        # process's MQTT broker).
        self._broker = broker

        self._engine: DynamicSimulationEngine | None = None
        self._runtime: CloneRuntime | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def serial_number(self) -> str:
        if self._engine is None:
            msg = "Panel not initialised — call start() first"
            raise RuntimeError(msg)
        return self._engine.serial_number

    @property
    def total_tabs(self) -> int:
        if self._engine is None:
            msg = "Panel not initialised — call start() first"
            raise RuntimeError(msg)
        return self._engine.total_tabs

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def engine(self) -> DynamicSimulationEngine | None:
        return self._engine

    @property
    def runtime(self) -> CloneRuntime | None:
        return self._runtime

    # ------------------------------------------------------------------
    # Battery state accessors — read-through to the emitter's last snapshot.
    # The simulator no longer models BESS state; the emitter's BESSDevice owns
    # SOC/SOE and active power, and writes them into snapshot.battery on every
    # publish. These helpers give the dashboard / HA-API a stable read path.
    # ------------------------------------------------------------------

    @property
    def has_battery(self) -> bool:
        if self._engine is None:
            return False
        return self._engine.has_battery

    @property
    def soc_percentage(self) -> float | None:
        snap = self._last_battery()
        return snap.soe_percentage if snap is not None else None

    @property
    def battery_active_power_w(self) -> float:
        """Positive = discharging, negative = charging."""
        snap = self._last_battery()
        return snap.active_power_w if snap is not None else 0.0

    def _last_battery(self) -> EbusBatterySnapshot | None:
        """First battery from the emitter's snapshot (the simulator models a
        single BESS per panel today). The emitter's Phase 2 reshape pluralised
        ``snapshot.battery`` into a dict keyed by ``instance_id``; we collapse
        to the first entry for the simulator's single-BESS world."""
        if self._runtime is None:
            return None
        last = self._runtime.emitter.last_snapshot
        if last is None:
            return None
        return next(iter(last.battery.values()), None)

    def get_power_summary(self) -> dict[str, Any] | None:
        """Power summary in the legacy shape dashboard / HA-API consumers expect.

        Live values (grid, pv, battery, consumption, SOC) are sourced from the
        emitter's last snapshot — the authoritative post-redesign location for
        panel state. The engine still owns the static envelope (grid_online
        flag, configured battery presence, shed/override sets, recorder bounds,
        clock acceleration, timezone, soc threshold)."""
        if self._engine is None:
            return None
        summary = self._engine.get_power_summary()
        snap = self._runtime.emitter.last_snapshot if self._runtime is not None else None
        if snap is not None:
            summary["grid_w"] = round(snap.meter.instant_grid_power_w, 1)
            summary["pv_w"] = round(snap.power_flows.pv or 0.0, 1)

            # Battery is now a dict keyed by instance_id. The simulator models a
            # single BESS per panel today, so collapse to the first entry. If
            # multiple natives ever land here, snapshot.power_flows.battery is
            # the authoritative panel-level aggregate — but the per-device SOC
            # would still need a policy decision (which one to surface).
            batt = next(iter(snap.battery.values()), None)
            if batt is not None:
                # Sign flip from emitter → SPAN dashboard convention:
                # emitter ``active_power_w`` is positive = discharging, negative = charging.
                # SPAN dashboards / HA-API consumers expect positive = charging
                # (panel→battery), negative = discharging (battery→panel). See SPAN
                # hardware sign convention reference in SpanPanel/span#184.
                summary["battery_w"] = round(-batt.active_power_w, 1)
                summary["soc_pct"] = (
                    round(batt.soe_percentage, 1) if batt.soe_percentage is not None else None
                )
            else:
                summary["battery_w"] = 0.0
                summary["soc_pct"] = None

            summary["consumption_w"] = round(snap.power_flows.site or 0.0, 1)
        return summary

    def update_bess_config(self, bess_yaml: BESSConfigYAML) -> None:
        """Apply a fresh BESS configuration mid-run (e.g. dashboard edit). The
        emitter's BESSDevice swaps configs; SOC/SOE state persists across the
        swap, the change takes effect on the next published tick."""
        if self._runtime is None:
            msg = "Panel not initialised — call start() first"
            raise RuntimeError(msg)
        emitter_runtime.update_bess_config_live(self._runtime, bess_yaml)

    async def start(self) -> str:
        engine = DynamicSimulationEngine(
            config_path=self._config_path,
            recorder=self._recorder,
        )
        await engine.initialize_async()
        self._engine = engine

        self._runtime = await emitter_runtime.start_clone(
            engine,
            broker=self._broker,
        )

        await emitter_runtime.publish_tick(self._runtime)

        self._running = True
        self._tick_task = asyncio.create_task(
            self._tick_loop(),
            name=f"tick-{engine.serial_number}",
        )

        _LOGGER.info(
            "Panel %s started (config=%s)",
            engine.serial_number,
            self._config_path.name,
        )
        return engine.serial_number

    async def stop(self, *, graceful: bool = True) -> None:
        self._running = False

        if self._tick_task is not None:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None

        if self._runtime is not None:
            try:
                await emitter_runtime.stop_clone(self._runtime, graceful=graceful)
            except Exception:
                _LOGGER.exception("Error stopping emitter runtime")
            self._runtime = None

        serial = self._engine.serial_number if self._engine else "unknown"
        self._engine = None
        _LOGGER.info("Panel %s stopped", serial)

    async def reload(self) -> str:
        _LOGGER.info("Reloading panel from %s", self._config_path.name)
        await self.stop()
        return await self.start()

    async def _tick_loop(self) -> None:
        assert self._engine is not None
        assert self._runtime is not None

        while self._running:
            await asyncio.sleep(self._tick_interval)
            try:
                await emitter_runtime.publish_tick(self._runtime)
            except Exception:
                _LOGGER.exception("Tick publish failed for %s", self._engine.serial_number)
