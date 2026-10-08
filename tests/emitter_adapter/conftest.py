"""Fixtures shared by the emitter adapter's tests."""

from __future__ import annotations

import pytest

from tests.emitter_adapter._fake_aiomqtt import FakeBroker


@pytest.fixture
def fake_broker(monkeypatch: pytest.MonkeyPatch) -> FakeBroker:
    """A broker the test can take away, behind every ``aiomqtt.Client`` built."""
    broker = FakeBroker()
    broker.install(monkeypatch)
    return broker
