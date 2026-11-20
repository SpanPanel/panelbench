"""A config PanelBench accepts is one a panel starts on.

Validation refuses, with a reason naming what is wrong, every section the panel's
definition reads that is not the shape its type says, and then builds the emitter as a
panel's start does, so every rule the emitter applies to a definition applies there too.
Nothing a config holds reaches the panel's start as an error naming nothing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

import pytest
import yaml

from panelbench import validation
from panelbench.validation import validate_yaml_config
from tests._helpers import CONFIGS, PANEL_CONFIG

if TYPE_CHECKING:
    from pathlib import Path

_MLO_48: Final = CONFIGS / "ref_MLO_48_TANDEMS.yaml"
# Stands for the first circuit's template in a path through `circuit_templates`.
_TEMPLATE: Final = "<template>"
# The working MAIN 32's PV circuit's template.
_PV_TEMPLATE: Final = "solar_production"


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
        (PANEL_CONFIG, ("panel_config", "total_tabs"), None, r"total_tabs must be the spaces"),
        (
            PANEL_CONFIG,
            ("panel_config", "total_tabs"),
            30,
            r"one of 8, 16, 24, 32, 40, 48, not 30",
        ),
        (PANEL_CONFIG, ("panel_config", "main_size"), [], r"main_size must be the main"),
        (PANEL_CONFIG, ("panel_config", "serial_number"), [], r"serial_number must be the"),
        (PANEL_CONFIG, ("bess", "nameplate_capacity_kwh"), None, r"nameplate_capacity_kwh must"),
        (PANEL_CONFIG, ("bess", "max_charge_w"), "fast", r"bess\.max_charge_w must be a finite"),
        (PANEL_CONFIG, ("bess", "nameplate_capacity_kwh"), 0, r"must be above 0, not 0"),
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
            ("circuit_templates", _PV_TEMPLATE, "energy_profile", "nameplate_capacity_w"),
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
        (PANEL_CONFIG, ("circuits", 0, "id"), [], r"id must be a name or a number"),
        (PANEL_CONFIG, ("panel_config", "latitude"), 122.4, r"latitude must be degrees from -90"),
        (PANEL_CONFIG, ("panel_config", "longitude"), 181, r"longitude must be degrees from -180"),
        (PANEL_CONFIG, ("panel_config", "soc_shed_threshold"), None, r"must be a percentage"),
        (
            PANEL_CONFIG,
            ("bess", "discharge_efficiency"),
            0,
            r"discharge_efficiency must be above 0",
        ),
        (PANEL_CONFIG, ("simulation_params", "time_acceleration"), "", r"time_acceleration must"),
        (PANEL_CONFIG, ("tab_synchronizations",), 0, r"tab_synchronizations must list groups"),
        (
            PANEL_CONFIG,
            ("circuit_templates", _PV_TEMPLATE, "energy_profile", "nameplate_capacity_w"),
            "6000",
            r"nameplate_capacity_w must be a number of watts, not '6000'",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "energy_profile", "initial_consumed_energy_wh"),
            [1, 2],
            r"initial_consumed_energy_wh must be a number of watt-hours",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "monthly_factors"),
            -1,
            r"monthly_factors must map each month to a factor",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "hvac_type"),
            [1, 2],
            r"hvac_type must name an HVAC type",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "time_of_day_profile"),
            {"enabled": True, "active_days": -1},
            r"active_days must list the days it runs",
        ),
        (PANEL_CONFIG, ("circuits", 0, "overrides"), "x", r"overrides must be a mapping"),
        (
            PANEL_CONFIG,
            ("circuits", 0, "overrides"),
            {"typical_power": "x"},
            r"as circuit 0 overrides it, energy_profile\.typical_power must be a number",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "cycling_pattern"),
            None,
            r"cycling_pattern must be a mapping, not None",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "smart_behavior"),
            "x",
            r"smart_behavior must be a mapping",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "smart_behavior"),
            {"responds_to_grid": True, "max_power_reduction": "x"},
            r"smart_behavior\.max_power_reduction must be a fraction",
        ),
        (PANEL_CONFIG, ("panel_config", "serial_number"), 5, r"serial, a string, not 5"),
        (
            PANEL_CONFIG,
            ("panel_config", "latitude"),
            None,
            r"latitude must be degrees from -90 to 90, not None",
        ),
        (
            PANEL_CONFIG,
            ("panel_config", "longitude"),
            "x",
            r"longitude must be degrees from -180 to 180, not 'x'",
        ),
        (PANEL_CONFIG, ("simulation_params",), None, r"simulation_params must be a mapping"),
        (
            PANEL_CONFIG,
            ("simulation_params", "noise_factor"),
            "x",
            r"noise_factor must be a number",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "energy_profile"),
            {"mode": "consumer"},
            r"energy_profile has no power_range",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "energy_profile", "power_range"),
            {"min": 50, "max": 150},
            r"power_range must be \[minimum, maximum\] in watts",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "energy_profile", "typical_power"),
            "100",
            r"energy_profile\.typical_power must be a number",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "energy_profile", "power_variation"),
            "x",
            r"energy_profile\.power_variation must be a number",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "cycling_pattern"),
            "x",
            r"cycling_pattern must be a mapping",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "cycling_pattern"),
            {"on_duration": "900"},
            r"cycling_pattern\.on_duration must be a number of seconds",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "time_of_day_profile"),
            "x",
            r"time_of_day_profile must be a mapping",
        ),
        (
            PANEL_CONFIG,
            ("circuit_templates", _TEMPLATE, "time_of_day_profile"),
            {"enabled": True, "hourly_multipliers": {0: None}},
            r"hourly_multipliers must map each hour to a factor",
        ),
        (PANEL_CONFIG, ("circuits", 0, "template"), [], r"references unknown template"),
    ],
)
def test_a_setting_of_the_wrong_shape_is_refused_by_name(
    source: Path, at: tuple[str | int, ...], value: object, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        validate_yaml_config(_with(source, at, value))


@pytest.mark.parametrize(
    ("at", "value"),
    [
        (("panel_config", "main_size"), "200"),
        (("panel_config", "total_tabs"), "32"),
        (("circuit_templates", _TEMPLATE, "breaker_rating"), "20"),
        (("circuit_templates", _TEMPLATE, "energy_profile", "nameplate_capacity_w"), None),
        (("circuit_templates", _TEMPLATE, "energy_profile", "nameplate_capacity_w"), "600"),
        (("circuits", 0, "id"), 1.5),
        (("bess", "max_charge_w"), "3500"),
        (("circuits", 0, "tabs"), [1.0]),
        (("circuit_templates", _TEMPLATE, "cycling_pattern"), {"duty_cycle": "0.5"}),
        (
            ("circuit_templates", _TEMPLATE, "time_of_day_profile"),
            {"enabled": True, "hourly_multipliers": {0: "0.5"}},
        ),
    ],
    ids=lambda value: "/".join(map(str, value)) if isinstance(value, tuple) else repr(value),
)
def test_a_setting_its_reader_converts_still_loads(
    at: tuple[str | int, ...], value: object
) -> None:
    """Each of these ran before the shape checks: the code reading it converts it, as a
    quoted number, or does not read it, as a load's empty rating. So it still loads."""
    validate_yaml_config(_with(PANEL_CONFIG, at, value))


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


def test_the_emitter_validation_builds_and_drops_logs_nothing_below_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its device tree is one no one will see, and at DEBUG it would bury a running
    panel's own records under thousands, on every edit; those still pass."""
    caplog.set_level(logging.DEBUG)

    validate_yaml_config(_raw(_MLO_48))
    logging.getLogger("homie").debug("a running panel's own record")

    assert [r.getMessage() for r in caplog.records if r.name == "homie"] == [
        "a running panel's own record"
    ]


def test_a_template_no_circuit_uses_is_checked_for_its_shape_alone() -> None:
    """Nothing reads it, so a profile it could not be modelled by is no failure."""
    config = _with(
        PANEL_CONFIG,
        ("circuit_templates", "unused"),
        {
            "relay_behavior": "controllable",
            "priority": "NICE_TO_HAVE",
            "energy_profile": {"mode": "consumer", "power_range": {"min": 0, "max": 100}},
        },
    )

    validate_yaml_config(config)


def test_the_filter_drops_only_records_below_warning_and_only_while_validating(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    homie = logging.getLogger("homie")
    marked = validation._VALIDATING.set(True)
    try:
        homie.debug("the discarded build's record")
        homie.warning("a warning")
    finally:
        validation._VALIDATING.reset(marked)
    homie.debug("a running panel's record")

    assert [r.getMessage() for r in caplog.records if r.name == "homie"] == [
        "a warning",
        "a running panel's record",
    ]
