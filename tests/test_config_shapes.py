"""A config PanelBench accepts is one a panel starts on.

Validation refuses, with a reason naming what is wrong, every section the panel's
definition reads that is not the shape its type says, and then builds the emitter as a
panel's start does, so every rule the emitter applies to a definition applies there too.
Nothing a config holds reaches the panel's start as an error naming nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
import yaml

from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS, PANEL_CONFIG

if TYPE_CHECKING:
    from pathlib import Path

_MLO_48: Final = CONFIGS / "ref_MLO_48_TANDEMS.yaml"
# Stands for the first circuit's template in a path through `circuit_templates`.
_TEMPLATE: Final = "<template>"


def _raw(path: Path) -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _child(config: dict[str, object], node: object, step: str | int) -> object:
    if isinstance(node, list) and isinstance(step, int):
        return node[step]
    assert isinstance(node, dict)
    if step == _TEMPLATE:
        circuits = config["circuits"]
        assert isinstance(circuits, list)
        step = str(circuits[0]["template"])
    return node[step]


def _with(path: Path, at: tuple[str | int, ...], value: object) -> dict[str, object]:
    """The config at *path* with *value* at *at*."""
    config = _raw(path)
    node: object = config
    for step in at[:-1]:
        node = _child(config, node, step)
    last = at[-1]
    if isinstance(node, list) and isinstance(last, int):
        node[last] = value
    else:
        assert isinstance(node, dict)
        node[last] = value
    return config


@pytest.mark.parametrize(
    ("source", "at", "value", "reason"),
    [
        (PANEL_CONFIG, ("bess",), "x", r"bess must be a mapping of its settings, not 'x'"),
        (PANEL_CONFIG, ("pv",), "x", r"pv must be a mapping"),
        (PANEL_CONFIG, ("evse",), 5, r"evse must be a mapping"),
        (PANEL_CONFIG, ("broker",), "x", r"broker must be a mapping"),
        (PANEL_CONFIG, ("simulation_params",), [], r"simulation_params must be a mapping"),
        (_MLO_48, ("lugs",), "x", r"lugs must be a mapping"),
        (_MLO_48, ("lugs", "upstream"), "x", r"lugs\.upstream must be a mapping"),
        (PANEL_CONFIG, ("panel_config", "site"), "x", r"panel_config\.site must be a mapping"),
        (PANEL_CONFIG, ("panel_config", "total_tabs"), None, r"total_tabs must be a whole"),
        (PANEL_CONFIG, ("panel_config", "main_size"), [], r"main_size must be the main"),
        (PANEL_CONFIG, ("panel_config", "serial_number"), [], r"serial_number must be the"),
        (PANEL_CONFIG, ("bess", "nameplate_capacity_kwh"), None, r"nameplate_capacity_kwh must"),
        (PANEL_CONFIG, ("bess", "max_charge_w"), "fast", r"bess\.max_charge_w must be a finite"),
        (PANEL_CONFIG, ("bess", "charge_hours"), 5, r"bess\.charge_hours must be a list"),
        (PANEL_CONFIG, ("bess", "discharge_hours"), ["x"], r"bess\.discharge_hours must be"),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "energy_profile"),
            "x",
            r"energy_profile must be a mapping",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "energy_profile", "nameplate_capacity_w"),
            None,
            r"nameplate_capacity_w must be a number of watts",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "breaker_rating"),
            [],
            r"breaker_rating must be the breaker's amps",
        ),
        (PANEL_CONFIG, ("circuits", 0, "tabs"), ["x"], r"must be a list of space numbers"),
        (PANEL_CONFIG, ("circuits", 0, "template"), [], r"references unknown template"),
    ],
)
def test_a_setting_of_the_wrong_shape_is_refused_by_name(
    source: Path, at: tuple[str | int, ...], value: object, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        validate_yaml_config(_with(source, at, value))


def test_a_battery_feed_naming_no_circuit_is_refused() -> None:
    """The emitter takes a feed naming nothing as it is, so PanelBench says what it names."""
    config = _with(PANEL_CONFIG, ("bess", "feed"), "no_such_circuit")

    with pytest.raises(ValueError, match=r"bess\.feed 'no_such_circuit' names no circuit"):
        validate_yaml_config(config)


@pytest.mark.parametrize(
    ("source", "at", "value", "reason"),
    [
        pytest.param(
            PANEL_CONFIG,
            ("circuits", 0, "unvalued"),
            ["foo/bar"],
            r"master_bedroom_lights key 'unvalued': its profile declares no foo/bar",
            id="an unvalued path the circuit profile declares none of",
        ),
        pytest.param(
            _MLO_48,
            ("circuits", 0, "feeds_role"),
            "GENERATOR",
            r"circuit_1 key 'feeds-role': .* GENERATOR",
            id="a feeds role the variant cannot book",
        ),
        pytest.param(
            _MLO_48,
            ("outside_meters",),
            [{"id": "service_ct", "unvalued": ["foo/bar"]}],
            r"service_ct key 'unvalued': its profile declares no foo/bar",
            id="an outside meter, named by its id",
        ),
    ],
)
def test_what_the_emitter_refuses_at_start_is_refused_with_its_reason(
    source: Path, at: tuple[str | int, ...], value: object, reason: str
) -> None:
    with pytest.raises(ValueError, match=rf"The emitter refuses this panel: {reason}"):
        validate_yaml_config(_with(source, at, value))


def test_a_circuit_with_a_number_for_its_id_is_named_by_it() -> None:
    config = _with(PANEL_CONFIG, ("circuits", 0, "unvalued"), ["foo/bar"])
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    circuits[0]["id"] = 7

    with pytest.raises(ValueError, match=r"refuses this panel: 7 key 'unvalued'"):
        validate_yaml_config(config)
