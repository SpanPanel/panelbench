"""Shutdown must reach the cleanup that clears retained MQTT topics.

`SimulatorApp.run` stops every panel in a `finally`, and stopping a panel is what
clears its retained topics (`stop_clone` -> `emitter.stop(clear_retained=True)`).
Before this was fixed, only Ctrl-C got there: SIGTERM — what
`scripts/run-local.sh --stop`, Docker, and the HA supervisor send — killed the
process outright, stranding a full retained tree on the broker."""

import asyncio
import signal
from pathlib import Path

import pytest

from span_panel_simulator.__main__ import _run_until_signalled
from span_panel_simulator.app import SimulatorApp


class _FakeApp:
    """Stands in for SimulatorApp with the same stop/run contract.

    `run` mirrors the real `_reload_watcher` shape: parked on an event, with the
    cleanup in a `finally` so the test can assert the cleanup actually ran."""

    def __init__(self) -> None:
        self._running = False
        self._reload_event = asyncio.Event()
        self.cleanup_ran = False

    async def run(self) -> None:
        self._running = True
        try:
            while self._running:
                await self._reload_event.wait()
                self._reload_event.clear()
                if not self._running:
                    break
        finally:
            self._running = False
            self.cleanup_ran = True

    async def stop(self) -> None:
        self._running = False
        self._reload_event.set()


@pytest.mark.asyncio
async def test_sigterm_runs_the_cleanup() -> None:
    app = _FakeApp()
    runner = asyncio.create_task(_run_until_signalled(app))
    await asyncio.sleep(0)  # let the handlers install and run() park on the event

    signal.raise_signal(signal.SIGTERM)

    await asyncio.wait_for(runner, timeout=5)
    assert app.cleanup_ran, "SIGTERM must reach the retained-topic cleanup"


@pytest.mark.asyncio
async def test_sigint_runs_the_cleanup() -> None:
    app = _FakeApp()
    runner = asyncio.create_task(_run_until_signalled(app))
    await asyncio.sleep(0)

    signal.raise_signal(signal.SIGINT)

    await asyncio.wait_for(runner, timeout=5)
    assert app.cleanup_ran, "SIGINT must reach the retained-topic cleanup"


@pytest.mark.asyncio
async def test_real_app_stop_wakes_the_reload_watcher(tmp_path: Path) -> None:
    """Against the real SimulatorApp, not the stand-in: the watcher parks on
    `_reload_event.wait()`, so clearing `_running` alone would leave it blocked
    and shutdown would hang until some unrelated reload happened to fire."""
    app = SimulatorApp(config_dir=tmp_path)
    app._running = True
    watcher = asyncio.create_task(app._reload_watcher())
    await asyncio.sleep(0)
    assert not watcher.done(), "watcher should be parked on the reload event"

    await app.stop()

    await asyncio.wait_for(watcher, timeout=5)


@pytest.mark.asyncio
async def test_real_app_stop_does_not_trigger_a_final_reload(tmp_path: Path) -> None:
    """`stop()` wakes the watcher with the same event a reload uses, so the
    watcher must re-check `_running` rather than run one more reload on the way
    out."""
    app = SimulatorApp(config_dir=tmp_path)
    reloads = 0

    async def counting_reload() -> None:
        nonlocal reloads
        reloads += 1

    app.reload = counting_reload  # type: ignore[method-assign]
    app._running = True
    watcher = asyncio.create_task(app._reload_watcher())
    await asyncio.sleep(0)

    await app.stop()
    await asyncio.wait_for(watcher, timeout=5)

    assert reloads == 0


@pytest.mark.asyncio
async def test_explicit_stop_runs_the_cleanup() -> None:
    """`stop()` must wake the watcher parked on the reload event, not just clear
    the flag — otherwise shutdown hangs until an unrelated reload fires."""
    app = _FakeApp()
    runner = asyncio.create_task(_run_until_signalled(app))
    await asyncio.sleep(0)

    await app.stop()

    await asyncio.wait_for(runner, timeout=5)
    assert app.cleanup_ran
