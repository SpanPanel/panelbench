"""Shared test fixtures for the simulator test suite.

Post-emitter cutover: the legacy snapshot/publisher fixtures have been removed because
the underlying types (SpanPanelSnapshot, HomiePublisher) no longer exist on the
simulator side — they live in the emitter package as Ebus*Snapshot. Tests that need
snapshot fixtures should construct them via emitter_adapter helpers or import the
Ebus*Snapshot types directly from ebus_emitter."""

from __future__ import annotations
