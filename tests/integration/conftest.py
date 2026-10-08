"""Fixtures shared by the integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.integration._mosquitto import MOSQUITTO, Mosquitto


@pytest.fixture
async def mosquitto(tmp_path: Path) -> AsyncIterator[Mosquitto]:
    if MOSQUITTO is None:
        pytest.fail("PANELBENCH_REQUIRE_MOSQUITTO is set, but mosquitto is not installed")
    broker = Mosquitto(MOSQUITTO, tmp_path)
    await broker.start()
    try:
        yield broker
    finally:
        await broker.stop()
