"""Stable ID derivation for emitter manifest entries.

Lifted from publisher.py so the simulator's UUID derivation matches what the legacy
publisher produced. UUID v5 with a fixed namespace ensures the same circuit_id always
produces the same UUID across simulator restarts."""

from __future__ import annotations

import uuid

_CIRCUIT_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def stable_circuit_uuid(circuit_id: str) -> str:
    """Return a deterministic dashless UUID for a circuit identifier."""
    return str(uuid.uuid5(_CIRCUIT_NAMESPACE, circuit_id)).replace("-", "")
