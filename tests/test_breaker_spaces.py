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

import copy
import dataclasses

import pytest
from ebus_panel_sim import TickInputs, load_reference_capture

from panelbench.hardware import publishes_shared_breakers, variant_for
from panelbench.validation import validate_yaml_config
from tests._helpers import default_config
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

    @pytest.mark.parametrize(
        ("circuits", "crowded"),
        [
            (
                (
                    _circuit("a", [10], "b", "c"),
                    _circuit("b", [10], "a", "c"),
                    _circuit("c", [10], "a", "b"),
                ),
                10,
            ),
            (
                (
                    _circuit("a", [9], "b", "p"),
                    _circuit("b", [9], "a", "p"),
                    _circuit("p", [9, 11], "a", "b"),
                ),
                9,
            ),
            (
                (
                    _circuit("p", [9, 11], "q", "r"),
                    _circuit("q", [9, 11], "p", "r"),
                    _circuit("r", [9, 11], "p", "q"),
                ),
                9,
            ),
        ],
        ids=[
            "three single-pole circuits on one space",
            "two single-pole circuits and a two-pole one on one space",
            "three two-pole circuits on one pair",
        ],
    )
    def test_a_space_holding_more_than_two_circuits_is_refused(
        self, circuits: tuple[dict[str, object], ...], crowded: int
    ) -> None:
        """A space holds a tandem's two single-pole circuits, a quad's two two-pole
        ones, or a tandem-quad's single-pole one and its two-pole one: two at most."""
        config = _panel(_GEN3_HARDWARE, *circuits)

        with pytest.raises(ValueError, match=rf"all list space {crowded}, but .* at most two"):
            validate_yaml_config(config)


def _main_32_style(message: str) -> None:
    """*message* is a refusal on a MAIN 32-style panel that says why and how to fix it."""
    assert f"hardware_version {_MAIN_32_HARDWARE!r} selects the 'span' variant" in message
    assert "set hardware_version: '3.0'" in message
    assert "ref_ template" in message
    assert "default_MLO_48_TANDEMS" in message
    assert "make it one circuit" in message


def _refusal(config: dict[str, object]) -> str:
    with pytest.raises(ValueError) as refused:
        validate_yaml_config(config)
    return str(refused.value)


class TestMain32PublishesOneCircuitPerSpace:
    """The ``span`` variant: no space belongs to two circuits."""

    def test_two_circuits_on_one_space_are_refused(self) -> None:
        config = _panel(_MAIN_32_HARDWARE, _circuit("lights", [10]), _circuit("outlets", [10]))

        message = _refusal(config)

        assert "'lights' and 'outlets' both list space 10" in message
        _main_32_style(message)

    def test_a_single_pole_circuit_on_a_two_pole_circuits_space_is_refused(self) -> None:
        config = _panel(_MAIN_32_HARDWARE, _circuit("dryer", [18, 20]), _circuit("outlets", [20]))

        message = _refusal(config)

        assert "'dryer' and 'outlets' both list space 20" in message
        _main_32_style(message)

    @pytest.mark.parametrize(
        "breaker", [_TANDEM, _QUAD, _TANDEM_QUAD], ids=["tandem", "quad", "tandem-quad"]
    )
    def test_a_breaker_published_gen3_style_is_refused(
        self, breaker: tuple[dict[str, object], ...]
    ) -> None:
        """It publishes no shared-with lists, so ``shared_with`` would publish nothing."""
        message = _refusal(_panel(_MAIN_32_HARDWARE, *breaker))

        assert "publishes no shared-with lists" in message
        assert "remove shared_with" not in message, "alone, that leads into the overlap refusal"
        _main_32_style(message)

    def test_an_unset_hardware_version_is_named_as_the_default(self) -> None:
        config = _panel(_MAIN_32_HARDWARE, *_TANDEM)
        config.pop("hardware_version")

        message = _refusal(config)

        assert "hardware_version is unset, so '1.2', which selects the 'span' variant" in message

    @pytest.mark.parametrize(
        "breaker",
        [
            _TANDEM,
            (_circuit("lights", [10]), _circuit("outlets", [10])),
        ],
        ids=["named to each other", "naming no one"],
    )
    def test_following_either_fix_loads_and_never_leads_into_the_other_refusal(
        self, breaker: tuple[dict[str, object], ...]
    ) -> None:
        """Both refusals offer the same two fixes, and each fix loads."""
        _main_32_style(_refusal(_panel(_MAIN_32_HARDWARE, *breaker)))
        lights, outlets = breaker

        as_gen3 = _panel(
            _GEN3_HARDWARE,
            {**lights, "shared_with": ["outlets"]},
            {**outlets, "shared_with": ["lights"]},
        )
        as_main_32 = _panel(
            _MAIN_32_HARDWARE, {k: v for k, v in lights.items() if k != "shared_with"}
        )

        validate_yaml_config(as_gen3)
        validate_yaml_config(as_main_32)

    def test_its_package_is_one_two_pole_circuit(self) -> None:
        validate_yaml_config(_panel(_MAIN_32_HARDWARE, _circuit("washer_dryer", [18, 20])))


def test_a_tandem_added_to_a_clone_of_the_main_40_template_says_how_to_fix_it() -> None:
    """The case a user meets first: a clone of ``default_MAIN_40`` (hardware 1.2), with
    a circuit copied onto a space it already holds as a tandem's other half.

    The refusal names the hardware and both fixes. Taking hardware 3.0 then meets only
    the variant's own commissioned-system refusal, whose advice loads.
    """
    config = default_config()
    [first, *_] = [c for c in config["circuits"] if len(c.get("tabs", [])) == 1]
    half = copy.deepcopy(first)
    half["id"] = f"{first['id']}_2"
    half["name"] = "Tandem half"
    first["shared_with"] = [half["id"]]
    half["shared_with"] = [first["id"]]
    config["circuits"].append(half)

    _main_32_style(_refusal(dict(config)))

    config["hardware_version"] = _GEN3_HARDWARE
    assert "commissioned_system" in _refusal(dict(config))
    for template in config["circuit_templates"].values():
        template.pop("commissioned_system", None)
    validate_yaml_config(config)


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
