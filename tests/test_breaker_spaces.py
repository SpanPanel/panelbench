"""Which circuits may list the same breaker space, by how the panel publishes a breaker.

SPAN publishes a multi-load breaker two ways (spanio/SPAN-API-Client-Docs#21):

- **MAIN 32** (the emitter's ``span`` variant) publishes each breaker space as one
  circuit with one rating, and a package across two spaces as one two-pole circuit. It
  publishes no ``shared-with-device-ids``. So no space there belongs to two circuits.
- **MAIN 16, MLO 24, MAIN 40 and MLO 48** (``span-alpha-test-b2``) publish one circuit
  per protected circuit. The circuits on one breaker name each other in
  ``meter/shared-with-device-ids`` and ``switch/shared-with-device-ids``. A
  single-pole circuit lists its own space and a two-pole one lists both. So a tandem
  is two circuits on one space, a quad is two two-pole circuits on the same two spaces,
  and a tandem-quad is a single-pole circuit on each of two spaces plus a two-pole
  circuit on both.

A config's ``shared_with`` is what publishes those lists. Validation accepts circuits
that share a space only where they are one breaker's circuits, named to each other, on
a panel that publishes them so. Anything else is a genuine overlap, and the panel would
publish two circuits on one space with nothing to say they are one breaker.
"""

from __future__ import annotations

import dataclasses

import pytest
from ebus_panel_sim import TickInputs, load_reference_capture

from panelbench.hardware import publishes_shared_breakers, variant_for
from panelbench.validation import validate_yaml_config
from tests.fidelity.comparator import publish_reference

_MAIN_32_HARDWARE = "1.2"
_GEN3_HARDWARE = "3.0"
_SERIAL = "sim-spaces-001"
# The pinned emitter's reference MLO 48, whose three tandems share their spaces.
_MLO_48_CAPTURE = "r202639-e"


def _circuit(circuit_id: str, tabs: list[int], *shared: str) -> dict[str, object]:
    circuit: dict[str, object] = {
        "id": circuit_id,
        "name": circuit_id.replace("_", " ").title(),
        "template": "load",
        "tabs": tabs,
    }
    if shared:
        circuit["shared_with"] = list(shared)
    return circuit


def _panel(hardware: str, *circuits: dict[str, object]) -> dict[str, object]:
    """A panel of *circuits* on the hardware *hardware* names, after release 202639."""
    return {
        "panel_config": {"serial_number": _SERIAL, "total_tabs": 32, "main_size": 200},
        "circuit_templates": {
            "load": {
                "energy_profile": {
                    "mode": "consumer",
                    "power_range": [0.0, 1800.0],
                    "typical_power": 100.0,
                    "power_variation": 0.1,
                },
                "relay_behavior": "controllable",
                "priority": "NEVER",
            }
        },
        "circuits": [_circuit("neighbour", [1]), *circuits],
        "firmware_version": "spanos3/r202639/03",
        "hardware_version": hardware,
    }


_TANDEM = (_circuit("lights", [10], "outlets"), _circuit("outlets", [10], "lights"))
_QUAD = (_circuit("heat_pump", [9, 11], "well_pump"), _circuit("well_pump", [9, 11], "heat_pump"))
_TANDEM_QUAD = (
    _circuit("outlets", [9], "lights", "compressor"),
    _circuit("lights", [11], "outlets", "compressor"),
    _circuit("compressor", [9, 11], "outlets", "lights"),
)


class TestGen3PanelsPublishOneCircuitPerProtectedCircuit:
    """The ``span-alpha-test-b2`` variant: one breaker's circuits may share its spaces."""

    @pytest.mark.parametrize(
        "breaker", [_TANDEM, _QUAD, _TANDEM_QUAD], ids=["tandem", "quad", "tandem-quad"]
    )
    def test_one_breakers_circuits_named_to_each_other_share_its_spaces(
        self, breaker: tuple[dict[str, object], ...]
    ) -> None:
        validate_yaml_config(_panel(_GEN3_HARDWARE, *breaker))

    def test_two_circuits_on_one_space_that_name_no_one_are_refused(self) -> None:
        config = _panel(_GEN3_HARDWARE, _circuit("lights", [10]), _circuit("outlets", [10]))

        with pytest.raises(ValueError, match=r"'lights' and 'outlets' both list space 10"):
            validate_yaml_config(config)

    def test_a_circuit_overlapping_a_two_pole_one_it_does_not_name_is_refused(self) -> None:
        config = _panel(_GEN3_HARDWARE, _circuit("dryer", [9, 11]), _circuit("outlets", [11]))

        with pytest.raises(ValueError, match=r"'dryer' and 'outlets' both list space 11"):
            validate_yaml_config(config)

    def test_sharing_named_one_way_is_refused(self) -> None:
        config = _panel(
            _GEN3_HARDWARE, _circuit("lights", [10], "outlets"), _circuit("outlets", [10])
        )

        with pytest.raises(ValueError, match="every circuit on one breaker names all the others"):
            validate_yaml_config(config)

    def test_a_tandem_quad_member_naming_only_some_of_its_siblings_is_refused(self) -> None:
        """Each circuit on a breaker names all the others, so a single-pole circuit names
        the other single-pole one as well as the two-pole one."""
        outlets, lights, compressor = _TANDEM_QUAD
        config = _panel(
            _GEN3_HARDWARE, {**outlets, "shared_with": ["compressor"]}, lights, compressor
        )

        with pytest.raises(ValueError, match="every circuit on one breaker names all the others"):
            validate_yaml_config(config)

    def test_a_breaker_whose_circuits_are_spaces_apart_is_refused(self) -> None:
        config = _panel(
            _GEN3_HARDWARE,
            _circuit("lights", [10], "outlets"),
            _circuit("outlets", [20], "lights"),
        )

        with pytest.raises(ValueError, match="one space, or one two-pole pair"):
            validate_yaml_config(config)

    def test_a_breaker_across_two_spaces_with_no_two_pole_circuit_is_refused(self) -> None:
        """Across two spaces a breaker is a two-pole package, so one of its circuits spans
        both; two single-pole circuits on neighbouring spaces are two breakers."""
        config = _panel(
            _GEN3_HARDWARE, _circuit("lights", [9], "outlets"), _circuit("outlets", [11], "lights")
        )

        with pytest.raises(ValueError, match="one space, or one two-pole pair"):
            validate_yaml_config(config)


class TestMain32PublishesOneCircuitPerSpace:
    """The ``span`` variant: no space belongs to two circuits."""

    def test_two_circuits_on_one_space_are_refused(self) -> None:
        config = _panel(_MAIN_32_HARDWARE, _circuit("lights", [10]), _circuit("outlets", [10]))

        with pytest.raises(ValueError, match="publishes each breaker space as one circuit"):
            validate_yaml_config(config)

    def test_a_single_pole_circuit_on_a_two_pole_circuits_space_is_refused(self) -> None:
        config = _panel(_MAIN_32_HARDWARE, _circuit("dryer", [18, 20]), _circuit("outlets", [20]))

        with pytest.raises(ValueError, match="publishes each breaker space as one circuit"):
            validate_yaml_config(config)

    @pytest.mark.parametrize(
        "breaker", [_TANDEM, _QUAD, _TANDEM_QUAD], ids=["tandem", "quad", "tandem-quad"]
    )
    def test_a_breaker_published_gen3_style_is_refused(
        self, breaker: tuple[dict[str, object], ...]
    ) -> None:
        """It publishes no shared-with lists, so ``shared_with`` would publish nothing."""
        with pytest.raises(ValueError, match="publishes no shared-with lists"):
            validate_yaml_config(_panel(_MAIN_32_HARDWARE, *breaker))

    def test_its_package_is_one_two_pole_circuit(self) -> None:
        validate_yaml_config(_panel(_MAIN_32_HARDWARE, _circuit("washer_dryer", [18, 20])))


@pytest.mark.parametrize("hardware", [_MAIN_32_HARDWARE, _GEN3_HARDWARE])
def test_the_rule_follows_what_the_variant_publishes(hardware: str) -> None:
    """``publishes_shared_breakers`` restates what each variant's circuit profile
    declares, so it is checked against the reference emitter itself.

    The reference MLO 48 holds circuits that share a breaker. Under each variant, its
    circuits publish ``meter/`` and ``switch/shared-with-device-ids`` exactly where
    the rule says that variant publishes them.
    """
    variant = variant_for(hardware)
    definition = dataclasses.replace(
        load_reference_capture(_MLO_48_CAPTURE).definition, variant=variant
    )
    published = publish_reference(
        definition, [TickInputs(current_time=0.0, grid_online=True, circuits={})]
    )
    sharing = {
        path
        for properties in published.values()
        for path in properties
        if path.endswith("/shared-with-device-ids")
    }

    expected = {"meter/shared-with-device-ids", "switch/shared-with-device-ids"}
    assert sharing == (expected if publishes_shared_breakers(variant) else set())
    assert publishes_shared_breakers(variant) is (hardware == _GEN3_HARDWARE)
