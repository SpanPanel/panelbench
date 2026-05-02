from unittest.mock import AsyncMock, MagicMock

import pytest
from ebus_emitter import SetterRegistry

from span_panel_simulator.emitter_adapter.setter_handlers import register_all


def _runtime_stub() -> MagicMock:
    rt = MagicMock()
    rt.emitter = AsyncMock()
    return rt


def test_register_all_registers_required_handlers() -> None:
    setters = SetterRegistry()
    register_all(setters, _runtime_stub())
    for ec, prop in (
        ("circuit", "switch/relay"),
        ("circuit", "priority/shed-priority"),
        ("circuit", "info/name"),
        ("panel", "pcs/dominant-power-source"),
    ):
        assert setters.get(ec, prop) is not None


@pytest.mark.asyncio
async def test_relay_handler_calls_set_property_override() -> None:
    setters = SetterRegistry()
    runtime = _runtime_stub()
    register_all(setters, runtime)

    handler = setters.get("circuit", "switch/relay")
    assert handler is not None
    await handler("circuit", "c1", "switch/relay", "CLOSED")
    runtime.emitter.set_property_override.assert_awaited_once_with(
        "circuit",
        "c1",
        "switch/relay",
        "CLOSED",
    )


@pytest.mark.asyncio
async def test_dom_power_source_handler_calls_emitter() -> None:
    setters = SetterRegistry()
    runtime = _runtime_stub()
    register_all(setters, runtime)

    handler = setters.get("panel", "pcs/dominant-power-source")
    assert handler is not None
    await handler("panel", "p1", "pcs/dominant-power-source", "BATTERY")
    runtime.emitter.set_property_override.assert_awaited_once_with(
        "panel",
        "p1",
        "pcs/dominant-power-source",
        "BATTERY",
    )
