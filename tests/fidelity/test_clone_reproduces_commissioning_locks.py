"""A clone of a panel with commissioning locks must publish those locks.

Cloning a real panel is the feature this repo exists for, and a lock is exactly the
kind of thing a clone loses quietly: nothing fails, the tree still validates, and the
copy simply offers a control the original does not. Both of this panel's locks are
carried on the wire only as the *absence* of `$settable`, so a translation that reads
values alone reproduces neither.

The two are independent by construction — `manifest_physics.relay_locked` and
`manifest_physics.never_backup` read separate metadata keys, and
`wire/graph_builder._INSTANCE_LOCKS` maps each to its own property — so the fixture
carries a circuit with each, one with both, and one with neither.

This goes end to end rather than asserting on the intermediate config: scrape ->
`translate_scraped_panel` -> YAML -> engine -> emitter -> published tree. A test that
stopped at the YAML would pass while `spec_generator` dropped the key on the floor,
which is the half of the path that had no coverage at all.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from ebus_sdk import DiscoveredDevice

from panelbench.clone import make_clone_serial, translate_scraped_panel, write_clone_config
from panelbench.emitter_adapter import runtime as emitter_runtime
from panelbench.emitter_adapter.instance_ids import stable_circuit_uuid
from panelbench.emitter_adapter.wire_capture import RecordingTransport, as_capture
from panelbench.engine import DynamicSimulationEngine
from panelbench.scraper import ScrapedPanel

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_SERIAL = "sim-locks-0001"
_CLONE_SERIAL = make_clone_serial(_SERIAL)
"""The serial the *clone* runs under, and so the panel that owns the published circuits.

A circuit device id is scoped to its owning panel, and the panel doing the publishing
here is the copy, not the original that was scraped.
"""
TYPE_PANEL = "energy.ebus.device.distribution-enclosure"
TYPE_CIRCUIT = "energy.ebus.device.circuit"


def _circuit(
    device_id: str,
    name: str,
    space: str,
    *,
    priority: str,
    relay_controllable: bool,
    never_backup: bool,
) -> DiscoveredDevice:
    """One circuit, published the way a panel publishes its commissioning state.

    `$settable` is present exactly when the corresponding lock is absent, and never
    published as an explicit `false`: Homie 5 defaults the attribute, so firmware omits
    it, and the two are the same claim.
    """
    values: dict[str, dict[str, str]] = {
        "info": {"name": name, "spaces": space},
        "breaker": {"rating": "20", "poles": "1"},
        "switch": {
            "relay": "CLOSED",
            "relay-requester": "CONFIGURATION" if not relay_controllable else "NONE",
            "relay-controllable": "true" if relay_controllable else "false",
        },
        "load-shed": {"priority": priority},
        "meter": {
            "active-power": "-120.0",
            "imported-energy": "0.0",
            "exported-energy": "0.0",
        },
        "pcs": {"managed": "true" if relay_controllable else "false", "priority": "0"},
    }
    settable: dict[str, set[str]] = {}
    if relay_controllable:
        settable["switch"] = {"relay"}
    if not never_backup:
        settable["load-shed"] = {"priority"}

    device = DiscoveredDevice(device_id)
    device.update_description(
        json.dumps(
            {
                "homie": "5.0",
                "name": name,
                "type": TYPE_CIRCUIT,
                "root": _SERIAL,
                "parent": _SERIAL,
                "children": [],
                "nodes": {
                    cap: {
                        "name": cap,
                        "properties": {
                            prop: (
                                {"name": prop, "datatype": "string", "settable": True}
                                if prop in settable.get(cap, set())
                                else {"name": prop, "datatype": "string"}
                            )
                            for prop in props
                        },
                    }
                    for cap, props in values.items()
                },
            }
        )
    )
    for cap, props in values.items():
        for prop, value in props.items():
            device.update_property(cap, prop, value)
    return device


# space -> (name, priority, relay_controllable, never_backup)
_SOURCE_CIRCUITS: dict[str, tuple[str, str, bool, bool]] = {
    "1": ("Kitchen Outlets", "OFF_GRID", True, False),
    "3": ("Solar Inverter", "NEVER", False, False),
    "5": ("Pool Pump", "OFF_GRID", True, True),
    "7": ("Well Pump", "OFF_GRID", False, True),
}


def _scraped() -> ScrapedPanel:
    panel = DiscoveredDevice(_SERIAL)
    children = [f"circuit-{space}" for space in _SOURCE_CIRCUITS]
    panel.update_description(
        json.dumps(
            {
                "homie": "5.0",
                "name": "Source Panel",
                "type": TYPE_PANEL,
                "root": _SERIAL,
                "children": children,
                "nodes": {
                    "info": {
                        "name": "info",
                        "properties": {
                            "serial-number": {"name": "serial", "datatype": "string"},
                            "model": {"name": "model", "datatype": "string"},
                        },
                    },
                    "breaker": {
                        "name": "breaker",
                        "properties": {"rating": {"name": "rating", "datatype": "integer"}},
                    },
                },
            }
        )
    )
    panel.update_property("info", "serial-number", _SERIAL)
    panel.update_property("info", "model", "MAIN_40")
    panel.update_property("breaker", "rating", "200")

    devices: dict[str, DiscoveredDevice] = {_SERIAL: panel}
    for space, (name, priority, controllable, never_backup) in _SOURCE_CIRCUITS.items():
        devices[f"circuit-{space}"] = _circuit(
            f"circuit-{space}",
            name,
            space,
            priority=priority,
            relay_controllable=controllable,
            never_backup=never_backup,
        )
    return ScrapedPanel(
        serial_number=_SERIAL, devices=devices, mqtts_port=8883, ca_pem=b"fake-ca-pem"
    )


async def _clone_and_publish(tmp_path: Path) -> dict[str, dict[str, str]]:
    """Translate the source panel, run the clone, and hand back what it published."""
    config = translate_scraped_panel(_scraped())
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    path = write_clone_config(config, config_dir, _SERIAL)

    engine = DynamicSimulationEngine(config_path=path)
    await engine.initialize_async()
    recorder = RecordingTransport()
    runtime = await emitter_runtime.start_clone(engine, transport=recorder)
    await emitter_runtime.publish_tick(runtime)
    return as_capture(recorder.retained)


def _declared(capture: dict[str, dict[str, str]], space: str) -> dict[str, object]:
    """The `$description` the cloned circuit at this breaker position published."""
    # `clone._translate_circuit` names a cloned circuit `circuit_<space>`, and its
    # Homie device id is derived from that name scoped to the panel that owns it.
    device_id = stable_circuit_uuid(_CLONE_SERIAL, f"circuit_{space}")
    document = json.loads(capture[device_id]["$description"])
    assert isinstance(document, dict), f"$description is not an object: {document!r}"
    return document


def _declaration(document: dict[str, object], capability: str, prop: str) -> dict[str, object]:
    """One property's declaration, narrowed a step at a time from the parsed document.

    `json.loads` returns `object`, and every level down to a declaration is somewhere a
    malformed capture could hold something other than a mapping. Checking each keeps the
    types honest without reaching for `Any`, and turns a shape surprise into a failure
    that names the level it happened at rather than an `AttributeError` further on.
    """
    node = document["nodes"]
    assert isinstance(node, dict), f"$description has no nodes object: {node!r}"
    capability_node = node[capability]
    assert isinstance(capability_node, dict), f"node {capability!r} is not an object"
    properties = capability_node["properties"]
    assert isinstance(properties, dict), f"node {capability!r} has no properties object"
    declaration = properties[prop]
    assert isinstance(declaration, dict), f"{capability}/{prop} is not an object"
    return declaration


def _settable(document: dict[str, object], capability: str, prop: str) -> bool:
    return _declaration(document, capability, prop).get("settable") is True


@pytest.mark.parametrize("space", sorted(_SOURCE_CIRCUITS))
async def test_the_clone_reproduces_both_commissioning_locks(tmp_path: Path, space: str) -> None:
    """Every published surface of both locks, on all four combinations at once.

    Asserting the four together is the point: the two locks are separate metadata keys
    read by separate predicates, so a translation that fused them would still satisfy
    the two single-lock circuits, and one that dropped the composition would still
    satisfy the unlocked one.
    """
    capture = await _clone_and_publish(tmp_path)
    _name, priority, relay_controllable, never_backup = _SOURCE_CIRCUITS[space]
    document = _declared(capture, space)
    values = capture[stable_circuit_uuid(_CLONE_SERIAL, f"circuit_{space}")]

    assert _settable(document, "switch", "relay") is relay_controllable
    assert values["switch/relay-controllable"] == ("true" if relay_controllable else "false")
    assert values["switch/relay-requester"] == ("NONE" if relay_controllable else "CONFIGURATION")

    assert _settable(document, "load-shed", "priority") is not never_backup
    assert values["load-shed/priority"] == priority


async def test_a_never_backup_circuit_clones_to_the_manifest_key(tmp_path: Path) -> None:
    """The intermediate the wire assertions above depend on.

    `never-backup` is the only thing the emitter reads, and the guide maps it onto
    exactly one thing — `load-shed/priority`'s `$settable`. It must accompany
    `default-priority: OFF_GRID`, which the emitter enforces at construction, so the
    cloned template has to carry the priority the source published, not a value the
    lock implies.
    """
    config = translate_scraped_panel(_scraped())
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)

    assert templates["clone_5"]["never_backup"] is True
    assert templates["clone_5"]["priority"] == "OFF_GRID"
    assert templates["clone_7"]["never_backup"] is True
    assert "never_backup" not in templates["clone_1"]
    assert "never_backup" not in templates["clone_3"]


async def test_a_self_contradictory_source_clones_without_the_lock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A panel that locks `priority` at a value other than `OFF_GRID` states two
    incompatible things, and the emitter refuses the pair at construction. Carrying the
    lock through would produce a clone that cannot start; dropping it silently would
    hide the source's mistake. Keep the published priority, drop the lock, say so."""
    scraped = _scraped()
    scraped.devices["circuit-3"] = _circuit(
        "circuit-3",
        "Solar Inverter",
        "3",
        priority="NEVER",
        relay_controllable=False,
        never_backup=True,
    )

    with caplog.at_level("WARNING"):
        config = translate_scraped_panel(scraped)

    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    assert "never_backup" not in templates["clone_3"]
    assert templates["clone_3"]["priority"] == "NEVER"
    assert "not OFF_GRID" in caplog.text
