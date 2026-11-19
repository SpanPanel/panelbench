"""The spaces a panel has follow the positions of the model it reports.

The SpanPanel integration's client warns of a panel whose circuits sit outside its
model's positions (``panel_models.PANEL_POSITIONS``), and not every model starts at
space 1. So the spaces PanelBench offers a new circuit, and lists as unmapped in a
clone or an imported definition, are the model's, never 1 to ``total_tabs``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from panelbench.dashboard.config_store import ConfigStore
from panelbench.panel_models import panel_spaces
from panelbench.reference_templates import reference_template

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig

# The reference captures of a MAIN 16 and a MAIN 40, whose spaces start at 9.
_MAIN_16 = "r202639-b"
_MAIN_40 = "r202639-c"


def _config(total_tabs: int, model: str | None = None) -> SimulationConfig:
    panel = f"panel_config: {{serial_number: s, total_tabs: {total_tabs}, main_size: 200"
    panel += f", model: {model}}}" if model else "}"
    config: SimulationConfig = yaml.safe_load(panel)
    return config


@pytest.mark.parametrize(
    ("model", "total_tabs", "first", "last"),
    [
        ("MAIN_16", 24, 9, 24),
        ("MLO_24", 24, 1, 24),
        ("MAIN_32", 32, 1, 32),
        ("MAIN_40", 48, 9, 48),
        ("MLO_48", 48, 1, 48),
    ],
)
def test_a_known_models_spaces_are_its_positions(
    model: str, total_tabs: int, first: int, last: int
) -> None:
    assert panel_spaces(_config(total_tabs, model)) == range(first, last + 1)


def test_a_model_without_positions_has_spaces_one_to_total_tabs() -> None:
    assert panel_spaces(_config(16, "UNKNOWN")) == range(1, 17)


def test_no_space_lies_beyond_total_tabs() -> None:
    """A config whose size falls short of its model's last position, such as a
    16-space config reported as a MAIN 16, has only the positions within it."""
    assert panel_spaces(_config(16)) == range(9, 17)


def test_a_clone_of_a_main_40_adds_its_circuit_inside_the_models_positions() -> None:
    store = ConfigStore()
    store.load_from_yaml(yaml.safe_dump(reference_template(_MAIN_40), sort_keys=False))
    free = store.get_unmapped_tabs()
    assert free
    assert min(free) >= 9

    store.add_entity("circuit")

    added = yaml.safe_load(store.export_yaml())["circuits"][-1]
    assert added["tabs"] == [min(free)]


@pytest.mark.parametrize("capture", [_MAIN_16, _MAIN_40])
def test_an_imported_definition_lists_no_unmapped_space_below_the_models_first(
    capture: str,
) -> None:
    unmapped = reference_template(capture)["unmapped_tabs"]
    assert isinstance(unmapped, list)
    assert all(space >= 9 for space in unmapped)
