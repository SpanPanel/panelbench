"""The spaces a panel has follow the positions of the model it reports.

The SpanPanel integration's client warns of a panel whose circuits sit outside its
model's positions (``panel_models.PANEL_POSITIONS``), and not every model starts at
space 1. So where a config names its model, as a clone and a reference template do,
the spaces PanelBench offers a new circuit, and lists as unmapped in a clone or an
imported definition, are that model's. A config with no ``model``, sized by its count
of spaces, keeps 1 to ``total_tabs``.
"""

from __future__ import annotations

import pytest
import yaml

from panelbench.dashboard.config_store import ConfigStore
from panelbench.panel_models import panel_spaces
from panelbench.reference_templates import reference_template

# The reference captures of a MAIN 16 and a MAIN 40, whose spaces start at 9.
_MAIN_16 = "r202639-b"
_MAIN_40 = "r202639-c"


def _panel(total_tabs: int, model: str | None = None) -> dict[str, object]:
    panel: dict[str, object] = {"serial_number": "s", "total_tabs": total_tabs, "main_size": 200}
    if model is not None:
        panel["model"] = model
    return panel


@pytest.mark.parametrize(
    ("model", "total_tabs", "first", "last"),
    [
        ("MAIN_16", 24, 9, 24),
        ("MLO_24", 24, 1, 24),
        ("MAIN_32", 32, 1, 32),
        ("MAIN_40", 48, 9, 48),
        ("MLO_48", 48, 1, 48),
        ("main_40", 48, 9, 48),
    ],
)
def test_a_known_models_spaces_are_its_positions(
    model: str, total_tabs: int, first: int, last: int
) -> None:
    assert panel_spaces(_panel(total_tabs, model)) == range(first, last + 1)


def test_a_model_without_positions_has_spaces_one_to_total_tabs() -> None:
    assert panel_spaces(_panel(16, "UNKNOWN")) == range(1, 17)


@pytest.mark.parametrize(
    ("model", "total_tabs", "first"), [("MAIN_40", 40, 9), ("MAIN_16", 16, 9)]
)
def test_a_named_model_short_of_its_last_space_keeps_its_first(
    model: str, total_tabs: int, first: int
) -> None:
    """A clone made before PanelBench knew the positions names its model but was sized
    by the model's numeric suffix, as a 2.8.0 clone of a MAIN 40 with no circuit above
    40 is: its spaces still start where the model's do, up to ``total_tabs``."""
    assert panel_spaces(_panel(total_tabs, model)) == range(first, total_tabs + 1)


@pytest.mark.parametrize("total_tabs", [16, 40])
def test_a_config_sized_by_its_count_of_spaces_keeps_spaces_from_1(total_tabs: int) -> None:
    """A config with no ``model`` and ``total_tabs`` 16 or 40 reports a MAIN 16 or MAIN 40
    but falls short of that model's last space, as a config written before PanelBench
    knew the positions does, so it keeps the spaces it was written with."""
    assert panel_spaces(_panel(total_tabs)) == range(1, total_tabs + 1)


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


def test_a_space_outside_the_models_is_refused_as_not_the_panels() -> None:
    """Not as "already assigned", which space 3 of a MAIN 40 is not."""
    store = ConfigStore()
    store.load_from_yaml(yaml.safe_dump(reference_template(_MAIN_40), sort_keys=False))
    with pytest.raises(ValueError, match="not one of this panel's spaces, 9 to 48"):
        store.add_entity_from_tabs([3])
