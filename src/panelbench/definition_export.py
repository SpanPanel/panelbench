"""Export a PanelBench config's makeup as a panel definition.

Loaded through the engine, exactly as a running panel loads it, so normalisation
and the ``sim-`` serial prefix apply and the definition names what that panel
actually publishes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ebus_panel_sim import PanelDefinition, dump_definition

from panelbench.emitter_adapter.definition import build_definition
from panelbench.engine import DynamicSimulationEngine


async def definition_for_config_file(path: Path) -> PanelDefinition:
    """The definition of the panel a saved config file runs as."""
    engine = DynamicSimulationEngine(config_path=path)
    await engine.initialize_async()
    return build_definition(engine.config)


def definition_text(definition: PanelDefinition) -> str:
    """*definition* as upstream writes it, through upstream's own dumper."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "definition.yaml"
        dump_definition(definition, path)
        return path.read_text(encoding="utf-8")
