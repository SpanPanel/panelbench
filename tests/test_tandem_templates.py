"""The ``*_TANDEMS`` templates' multi-load breakers, as each SPAN model publishes them.

SPAN describes both ways on spanio/SPAN-API-Client-Docs#21:

- A **MAIN 32** publishes each breaker space as one circuit with one rating, the first
  of the package's, so a 20-20 tandem and a 20-30 package both publish ``20``. The
  space's one sensor reads both breakers together, and a two-pole circuit's
  ``meter/current`` is the average of its two legs. ``default_MAIN_32_TANDEMS.yaml``
  holds a 20-20 tandem and a 20-30 package across two spaces, each loaded past the
  20 A it publishes, as a consumer judging the reading against that rating would see it.
- A **MAIN 16, MLO 24, MAIN 40 or MLO 48** publishes one circuit per protected
  circuit, each with its own rating, naming its siblings in ``meter/`` and
  ``switch/shared-with-device-ids``. ``default_MLO_48_TANDEMS.yaml`` is the pinned
  emitter's reference MLO 48 imported, with its three tandems, plus a 15-20 tandem, a
  quad and a tandem-quad.

Both still publish each circuit's own reading. That every circuit on a breaker
publishes the breaker's one combined reading is the emitter's to change, and nothing
here depends on it.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import pytest
import yaml

from panelbench.clone import translate_panel_tree
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import (
    capture_retained,
    discovered_devices,
    recorded_panel,
)
from panelbench.hardware import panel_variant, publishes_shared_breakers
from panelbench.reference_templates import reference_template, template_capture_names
from panelbench.validation import validate_yaml_config
from tests._helpers import published, write_config

if TYPE_CHECKING:
    from collections.abc import Mapping

    from panelbench.config_types import CircuitDefinitionExtended, SimulationConfig

_CONFIGS: Final = Path(__file__).resolve().parents[1] / "configs"
_SHIPPED: Final = sorted(_CONFIGS.glob("default_*.yaml"))
_MAIN_32: Final = _CONFIGS / "default_MAIN_32_TANDEMS.yaml"
_MLO_48: Final = _CONFIGS / "default_MLO_48_TANDEMS.yaml"

# Where the shipped panels sit, so a day of their loads is a day there.
_LOCAL: Final = ZoneInfo("America/Los_Angeles")
_DAY: Final = datetime(2026, 6, 15, tzinfo=_LOCAL)
_LINE_V: Final = 120.0


def _load(path: Path) -> SimulationConfig:
    config: SimulationConfig = yaml.safe_load(path.read_text(encoding="utf-8"))
    return config


def _circuit(config: SimulationConfig, circuit_id: str) -> CircuitDefinitionExtended:
    [circuit] = [c for c in config["circuits"] if c["id"] == circuit_id]
    return circuit


def _rating(config: SimulationConfig, circuit: CircuitDefinitionExtended) -> int:
    rating = circuit.get(
        "breaker_rating", config["circuit_templates"][circuit["template"]].get("breaker_rating")
    )
    assert isinstance(rating, int)
    return rating


async def _day_of_power(path: Path, circuit_id: str) -> list[tuple[int, float]]:
    """What *circuit_id* draws through a June day, every five minutes, by local hour.

    The deterministic path modelling takes, so the shape is the template's and not the
    tick's noise.
    """
    runtime, _ = await recorded_panel(path)
    steps = range(0, 24 * 60, 5)
    return [
        (
            (_DAY + timedelta(minutes=m)).hour,
            runtime.engine.modelled_circuit_power(
                circuit_id, (_DAY + timedelta(minutes=m)).timestamp()
            ),
        )
        for m in steps
    ]


async def _published_at(
    tmp_path: Path, config: SimulationConfig, local_time: str
) -> Mapping[str, bytes]:
    """What *config* publishes after one tick at *local_time* on the June day."""
    at = copy.deepcopy(config)
    at["simulation_params"]["use_simulation_time"] = True
    at["simulation_params"]["simulation_start_time"] = f"2026-06-15T{local_time}"
    runtime, recorder = await recorded_panel(write_config(tmp_path / "panel.yaml", at))
    await emitter_runtime.publish_tick(runtime)
    return recorder.retained


def test_every_shipped_template_runs_on_serials_of_its_own() -> None:
    """So any of them can run beside another, or beside a reference panel, on one broker:
    no panel serial is shared, and no battery serial, which names a device of its own."""
    shipped = [_load(path) for path in _SHIPPED]
    panels = [c["panel_config"]["serial_number"] for c in shipped]
    references = [
        str(_panel_serial(reference_template(name))) for name in template_capture_names()
    ]
    batteries = [str(c["bess"]["serial_number"]) for c in shipped if c.get("bess")]

    assert len(set(panels)) == len(panels), panels
    assert not set(panels) & set(references)
    assert len(set(batteries)) == len(batteries), batteries


def _panel_serial(config: Mapping[str, object]) -> object:
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    return panel["serial_number"]


class TestMain32Tandems:
    """One circuit per breaker space, at the package's first rating."""

    _TANDEM = "tandem_20_20"
    _PACKAGE = "washer_dryer"

    def test_it_publishes_no_shared_with_lists(self) -> None:
        config = _load(_MAIN_32)

        assert not publishes_shared_breakers(panel_variant(config))
        assert not [c["id"] for c in config["circuits"] if c.get("shared_with")]

    def test_the_tandem_is_one_single_space_circuit_at_its_first_rating(self) -> None:
        config = _load(_MAIN_32)
        tandem = _circuit(config, self._TANDEM)

        assert tandem["name"] == "Tandem 20-20"
        assert len(tandem["tabs"]) == 1
        assert _rating(config, tandem) == 20

    def test_the_package_is_one_two_pole_circuit_at_its_first_rating(self) -> None:
        config = _load(_MAIN_32)
        package = _circuit(config, self._PACKAGE)

        assert package["name"] == "Washer & Dryer 20-30"
        first, second = sorted(package["tabs"])
        assert second - first == 2, "a two-pole pair, one space on each leg"
        assert _rating(config, package) == 20

    def test_no_space_is_listed_by_two_circuits(self) -> None:
        config = _load(_MAIN_32)
        listed = [t for c in config["circuits"] for t in c["tabs"]]

        assert len(listed) == len(set(listed))
        assert not set(listed) & set(config["unmapped_tabs"])
        assert set(listed) | set(config["unmapped_tabs"]) == set(range(1, 33))

    @pytest.mark.asyncio
    async def test_the_tandems_one_reading_often_runs_past_its_rating(self) -> None:
        """Two 20 A kitchen branches read together: 31.7 A steady at the dinner peak,
        27.9-35.5 A with each tick's jitter, and past the 20 A the circuit publishes
        for several hours of the day. Measured here on the steady path."""
        day = await _day_of_power(_MAIN_32, self._TANDEM)
        amps = [watts / _LINE_V for _, watts in day]
        hours_past = {hour for hour, watts in day if watts / _LINE_V > 20}

        assert max(amps) == pytest.approx(31.7, abs=0.05)
        assert len(hours_past) >= 4, sorted(hours_past)

    @pytest.mark.asyncio
    async def test_the_packages_average_current_passes_its_rating_on_dryer_cycles(
        self,
    ) -> None:
        """The two legs' average, the circuit's power over 240 V, passes 20 A while the
        dryer heats, 22.5 A steady at its peak, and stays under it while only the washer
        runs. Measured on the steady path; the power range stops the jitter at 25 A."""
        day = await _day_of_power(_MAIN_32, self._PACKAGE)
        average = [watts / (2 * _LINE_V) for _, watts in day]

        assert max(average) == pytest.approx(22.5, abs=0.05)
        assert min(average) == 0.0, "idle outside laundry hours"

    @pytest.mark.parametrize(
        ("circuit_id", "volts", "busy"),
        [(_TANDEM, _LINE_V, "18:00:00"), (_PACKAGE, 2 * _LINE_V, "15:00:00")],
        ids=["tandem at dinner", "package on laundry day"],
    )
    @pytest.mark.asyncio
    async def test_each_publishes_one_circuit_one_rating_and_a_current_from_its_power(
        self, tmp_path: Path, circuit_id: str, volts: float, busy: str
    ) -> None:
        """``meter/current`` is the power over the circuit's voltage, which on a two-pole
        circuit is the average of its legs, as a MAIN 32 publishes it. Taken at an hour
        the circuit is busy, so the reading is not a trivial zero."""
        config = _load(_MAIN_32)
        circuit = _circuit(config, circuit_id)
        retained = await _published_at(tmp_path, config, busy)
        uuid = stable_circuit_uuid(config["panel_config"]["serial_number"], circuit_id)

        def value(path: str) -> str:
            return retained[f"ebus/5/{uuid}/{path}"].decode()

        assert value("info/spaces") == ",".join(str(t) for t in circuit["tabs"])
        assert value("breaker/rating") == "20"
        assert value("breaker/poles") == str(len(circuit["tabs"]))
        assert not [t for t in retained if t.startswith(f"ebus/5/{uuid}/") and "shared-with" in t]
        power = abs(float(value("meter/active-power")))
        assert power > 0
        assert float(value("meter/current")) == pytest.approx(power / volts, abs=0.1)


# Each multi-load breaker on the MLO 48, by the spaces it fills, with each circuit's
# spaces and rating. The first three are the reference panel's own.
_MLO_48_BREAKERS: Final[dict[str, dict[str, tuple[list[int], int]]]] = {
    "15-15 tandem at 44": {"circuit_44": ([44], 15), "circuit_44_2": ([44], 15)},
    "20-20 tandem at 46": {"circuit_46": ([46], 20), "circuit_46_2": ([46], 20)},
    "15-10 tandem at 48": {"circuit_48": ([48], 15), "circuit_48_2": ([48], 10)},
    "15-20 tandem at 37": {"circuit_37": ([37], 20), "circuit_37_2": ([37], 15)},
    "quad at 27 and 29": {"circuit_27": ([27, 29], 20), "circuit_27_2": ([27, 29], 30)},
    "tandem-quad at 31 and 33": {
        "circuit_31": ([31], 20),
        "circuit_33": ([33], 15),
        "circuit_31_2": ([31, 33], 20),
    },
}
# The breakers the MLO 48 adds, and the reference panel's circuits they replace.
_ADDED_BREAKERS: Final = ("15-20 tandem at 37", "quad at 27 and 29", "tandem-quad at 31 and 33")
_ADDED: Final = {c for name in _ADDED_BREAKERS for c in _MLO_48_BREAKERS[name]}
_REPLACED: Final = {"circuit_27", "circuit_29", "circuit_31", "circuit_33", "circuit_37"}
_REFERENCE: Final = "r202639-e"


def _reference() -> SimulationConfig:
    """The reference MLO 48 as PanelBench imports it, read as a template file is read."""
    config: SimulationConfig = yaml.safe_load(
        yaml.safe_dump(reference_template(_REFERENCE), sort_keys=False)
    )
    return config


# What a circuit is commissioned as, kept from the reference panel, and what PanelBench
# models on top of it, replaced with synthetic values.
_PANEL_SHAPE: Final = (
    "total_tabs",
    "main_size",
    "vendor_name",
    "model",
    "display_name",
    "wifi_link",
    "ethernet_link",
    "wifi_ssid",
    "off_grid_import_limit_enablement",
    "operator_import_limit_enablement",
    "unvalued",
)
_CIRCUIT_SHAPE: Final = (
    "tabs",
    "pcs_priority",
    "tags",
    "locations",
    "dedicated",
    "nominal_voltage",
    "protection_functions",
    "shared_with",
    "max_current_a",
    "user_max_charge_current_a",
    "device_unvalued",
)
_TEMPLATE_SHAPE: Final = ("relay_behavior", "priority", "breaker_rating", "device_type")


class TestMlo48Tandems:
    """One circuit per protected circuit, siblings named to each other."""

    def test_it_keeps_the_reference_panels_shape(self) -> None:
        """Imported from the reference definition: the panel and every circuit it keeps
        are commissioned as the reference panel's are."""
        shipped = _load(_MLO_48)
        reference = _reference()

        for key in ("firmware_version", "hardware_version", "lugs"):
            assert shipped.get(key) == reference.get(key), key
        for key in _PANEL_SHAPE:
            assert shipped["panel_config"].get(key) == reference["panel_config"].get(key), key
        kept = {c["id"]: c for c in reference["circuits"] if c["id"] not in _REPLACED}
        assert {c["id"] for c in shipped["circuits"]} == kept.keys() | _ADDED
        for circuit_id, theirs in kept.items():
            ours = _circuit(shipped, circuit_id)
            for key in _CIRCUIT_SHAPE:
                assert ours.get(key) == theirs.get(key), (circuit_id, key)
            our_template = shipped["circuit_templates"][ours["template"]]
            their_template = reference["circuit_templates"][theirs["template"]]
            for key in _TEMPLATE_SHAPE:
                assert our_template.get(key) == their_template.get(key), (circuit_id, key)

    def test_it_is_a_gen3_panel_that_publishes_shared_breakers(self) -> None:
        config = _load(_MLO_48)

        assert config["hardware_version"] == "3.0"
        assert publishes_shared_breakers(panel_variant(config))
        validate_yaml_config(config)

    def test_none_of_the_reference_panels_energy_carries_over(self) -> None:
        """Its registers start from PanelBench's estimate, not the reference panel's."""
        templates = _load(_MLO_48)["circuit_templates"].values()

        assert not [
            t
            for t in templates
            if {"initial_consumed_energy_wh", "initial_produced_energy_wh"}
            & t["energy_profile"].keys()
        ]

    def test_every_circuit_draws_a_load(self) -> None:
        """The reference panel imports at 0 W everywhere; this one models each load."""
        idle = [
            name
            for name, t in _load(_MLO_48)["circuit_templates"].items()
            if t["energy_profile"]["typical_power"] <= 0
        ]

        assert not idle

    @pytest.mark.parametrize("breaker", _MLO_48_BREAKERS, ids=list(_MLO_48_BREAKERS))
    def test_each_breakers_circuits_list_its_spaces_at_their_own_ratings(
        self, breaker: str
    ) -> None:
        config = _load(_MLO_48)

        for circuit_id, (tabs, rating) in _MLO_48_BREAKERS[breaker].items():
            circuit = _circuit(config, circuit_id)
            assert sorted(circuit["tabs"]) == tabs, circuit_id
            assert _rating(config, circuit) == rating, circuit_id

    @pytest.mark.parametrize("breaker", _MLO_48_BREAKERS, ids=list(_MLO_48_BREAKERS))
    def test_each_breakers_circuits_name_each_other_and_share_one_relay(
        self, breaker: str
    ) -> None:
        """Wired both ways, each naming every other. They share one relay, so they share
        its relay behaviour and its priority too."""
        config = _load(_MLO_48)
        members = set(_MLO_48_BREAKERS[breaker])
        circuits = [_circuit(config, c) for c in sorted(members)]
        templates = [config["circuit_templates"][c["template"]] for c in circuits]

        for circuit in circuits:
            assert set(circuit.get("shared_with", [])) == members - {circuit["id"]}, circuit["id"]
        assert len({t["relay_behavior"] for t in templates}) == 1
        assert len({t["priority"] for t in templates}) == 1
        assert len({c.get("pcs_priority") for c in circuits}) == 1

    def test_no_other_circuit_shares_a_space(self) -> None:
        config = _load(_MLO_48)
        on_breakers = {c for breaker in _MLO_48_BREAKERS.values() for c in breaker}

        assert {c["id"] for c in config["circuits"] if c.get("shared_with")} == on_breakers
        listed = [t for c in config["circuits"] if c["id"] not in on_breakers for t in c["tabs"]]
        shared = {
            t
            for breaker in _MLO_48_BREAKERS.values()
            for tabs, _ in breaker.values()
            for t in tabs
        }
        assert len(listed) == len(set(listed))
        assert not set(listed) & shared
        assert set(listed) | shared == set(range(1, 49))

    @pytest.mark.asyncio
    async def test_every_breakers_circuits_publish_their_siblings(self) -> None:
        """Each publishes its spaces, its own rating, and both shared-with lists naming
        its siblings' device ids."""
        config = _load(_MLO_48)
        serial = config["panel_config"]["serial_number"]
        runtime, recorder = await recorded_panel(_MLO_48)
        await emitter_runtime.publish_tick(runtime)

        for breaker in _MLO_48_BREAKERS.values():
            for circuit_id, (tabs, rating) in breaker.items():
                uuid = stable_circuit_uuid(serial, circuit_id)
                siblings = {stable_circuit_uuid(serial, c) for c in breaker if c != circuit_id}
                assert published(recorder, uuid, "info/spaces") == ",".join(map(str, tabs))
                assert published(recorder, uuid, "breaker/rating") == str(rating)
                for node in ("meter", "switch"):
                    named = published(recorder, uuid, f"{node}/shared-with-device-ids")
                    assert set(named.split(",")) == siblings, (circuit_id, node)

    @pytest.mark.asyncio
    async def test_only_the_breakers_circuits_publish_shared_with_lists(self) -> None:
        """A circuit on a standard breaker omits them, as SPAN publishes it."""
        config = _load(_MLO_48)
        serial = config["panel_config"]["serial_number"]
        retained = await capture_retained(_MLO_48)
        on_breakers = {
            stable_circuit_uuid(serial, c)
            for breaker in _MLO_48_BREAKERS.values()
            for c in breaker
        }

        sharing = {t.split("/")[2] for t in retained if t.endswith("/shared-with-device-ids")}

        assert sharing == on_breakers

    @pytest.mark.asyncio
    async def test_a_clone_of_it_loads_with_every_breaker_as_published(self) -> None:
        """Cloned from its published tree, as a newer-model panel is cloned: the clone
        passes validation and holds each breaker's circuits, named to each other, at
        their spaces and ratings. The reference captures have no quad or tandem-quad;
        this panel does."""
        config = _load(_MLO_48)
        devices = discovered_devices(await capture_retained(_MLO_48))

        cloned: SimulationConfig = yaml.safe_load(
            yaml.safe_dump(translate_panel_tree(config["panel_config"]["serial_number"], devices))
        )

        validate_yaml_config(cloned)
        by_name = {c["name"]: c for c in cloned["circuits"]}
        assert len(by_name) == len(cloned["circuits"]), "names tell the circuits apart"
        for breaker in _MLO_48_BREAKERS.values():
            names = {_circuit(config, c)["name"] for c in breaker}
            for circuit_id, (tabs, rating) in breaker.items():
                clone = by_name[_circuit(config, circuit_id)["name"]]
                siblings = {by_name[n]["id"] for n in names} - {clone["id"]}
                assert sorted(clone["tabs"]) == tabs, circuit_id
                assert _rating(cloned, clone) == rating, circuit_id
                assert set(clone.get("shared_with", [])) == siblings, circuit_id
