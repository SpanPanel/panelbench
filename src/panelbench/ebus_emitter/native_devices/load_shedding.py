"""Native load-shedding controller.

When the grid goes offline, circuits with shedding priorities below the active
SOC threshold are forcibly opened. The shedding decisions are emitter-domain
because they're a deterministic configured response: given (grid_state, BESS
SOC, per-circuit priority), the shed set is computable without producer
intervention.

``decide_shed`` is a pure function used by ``Emitter.publish_tick`` to drive
``RelayResolver``. When the operator has a /set override on a sheddable
circuit, the ``RelayResolver`` honors operator intent (per the v0.3.0
precedence rule: always-on > /set > load-shed > default-CLOSED). Load-shed
only takes effect when there's no operator override."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(slots=True)
class LoadSheddingConfig:
    """When grid is offline, circuits with these priorities are shed in order:
    first OFF_GRID, then SOC_THRESHOLD when BESS SOC drops below soc_threshold_pct.
    NEVER-priority circuits are never shed."""

    soc_threshold_pct: float = 20.0


@dataclass(slots=True)
class LoadSheddingDevice:
    """Per-tick load-shedding policy."""

    config: LoadSheddingConfig

    def update_config(self, config: LoadSheddingConfig) -> None:
        self.config = config

    def decide_shed(
        self,
        *,
        grid_online: bool,
        bess_soc_pct: float | None,
        priorities: Mapping[str, str],
    ) -> set[str]:
        """Return the set of circuit instance_ids the policy wants OPEN.

        - On-grid: always empty (nothing is shed when grid is up).
        - Off-grid: ``OFF_GRID`` priority always shed; ``SOC_THRESHOLD`` shed
          when BESS SOC is below ``soc_threshold_pct`` (or when no BESS exists).
        - ``NEVER`` priority is never shed."""
        if grid_online:
            return set()
        soc_low = bess_soc_pct is None or bess_soc_pct < self.config.soc_threshold_pct
        shed: set[str] = set()
        for cid, priority in priorities.items():
            p = (priority or "").upper()
            if p == "OFF_GRID" or (p == "SOC_THRESHOLD" and soc_low):
                shed.add(cid)
        return shed
