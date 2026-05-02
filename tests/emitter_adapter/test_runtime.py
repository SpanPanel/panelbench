from unittest.mock import AsyncMock, MagicMock

import pytest

from span_panel_simulator.emitter_adapter.runtime import (
    CloneRuntime,
    force_grid_offline,
    force_grid_online,
    reset_property_override,
)


@pytest.mark.asyncio
async def test_force_grid_offline_calls_emitter_with_offline() -> None:
    runtime = MagicMock(spec=CloneRuntime)
    runtime.emitter = AsyncMock()
    await force_grid_offline(runtime)
    runtime.emitter.force_grid_state.assert_awaited_once_with("OFFLINE")


@pytest.mark.asyncio
async def test_force_grid_online_passes_none() -> None:
    runtime = MagicMock(spec=CloneRuntime)
    runtime.emitter = AsyncMock()
    await force_grid_online(runtime)
    runtime.emitter.force_grid_state.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_reset_property_override_calls_emitter() -> None:
    runtime = MagicMock(spec=CloneRuntime)
    runtime.emitter = AsyncMock()
    await reset_property_override(runtime, "circuit", "c1", "switch/relay")
    runtime.emitter.clear_property_override.assert_awaited_once_with(
        "circuit",
        "c1",
        "switch/relay",
    )
