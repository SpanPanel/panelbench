"""Setter handler registration. Routes Homie /set responses through emitter override APIs.

Each handler is a one-liner that records a sticky property override on the emitter via
``emitter.set_property_override``. The emitter applies the debounce + consume-on-direction-
change rule on subsequent ticks.

Profile mappings:
- ``circuit.switch/relay``           — open/close a circuit relay (controllable circuits).
- ``circuit.priority/shed-priority`` — set shed priority (NEVER / SOC_THRESHOLD / OFF_GRID).
- ``circuit.info/name``              — rename a circuit.
- ``panel.pcs/dominant-power-source``— operator-selected dominant power source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ebus_emitter import SetterRegistry

    from span_panel_simulator.emitter_adapter.runtime import CloneRuntime


def register_all(setters: SetterRegistry, runtime: CloneRuntime) -> None:
    async def on_circuit_relay(
        entity_class: str,
        instance_id: str,
        prop_path: str,
        value: object,
    ) -> None:
        await runtime.emitter.set_property_override(
            entity_class,
            instance_id,
            prop_path,
            value,
        )

    async def on_shed_priority(
        entity_class: str,
        instance_id: str,
        prop_path: str,
        value: object,
    ) -> None:
        await runtime.emitter.set_property_override(
            entity_class,
            instance_id,
            prop_path,
            value,
        )

    async def on_circuit_name(
        entity_class: str,
        instance_id: str,
        prop_path: str,
        value: object,
    ) -> None:
        await runtime.emitter.set_property_override(
            entity_class,
            instance_id,
            prop_path,
            value,
        )

    async def on_dom_power_source(
        entity_class: str,
        instance_id: str,
        prop_path: str,
        value: object,
    ) -> None:
        await runtime.emitter.set_property_override(
            entity_class,
            instance_id,
            prop_path,
            value,
        )

    setters.register("circuit", "switch/relay", on_circuit_relay)
    setters.register("circuit", "priority/shed-priority", on_shed_priority)
    setters.register("circuit", "info/name", on_circuit_name)
    setters.register("panel", "pcs/dominant-power-source", on_dom_power_source)
