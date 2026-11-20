"""Import a panel definition as a PanelBench config.

A definition says what a panel *is*; a PanelBench config also says how it
behaves. The makeup is read the way PanelBench reads any panel: upstream's
emitter publishes the definition, and ``clone.translate_panel_tree`` translates
that tree exactly as it translates a live panel's, so PanelBench keeps one rule
for reading a panel rather than two. Behaviour comes from the translator's
defaults. What a published tree does not carry — the battery's dispatch settings
and the load-shed threshold — is then taken from the definition itself.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from ebus_panel_sim import (
    EmitterError,
    PanelDefinition,
    SetterRegistry,
    TickInputs,
    load_definition,
)

from panelbench.clone import translate_panel_tree
from panelbench.emitter_adapter.definition import (
    bess_dispatch_yaml,
    construct_emitter,
    load_shedding_yaml,
)
from panelbench.emitter_adapter.wire_capture import RecordingTransport, discovered_devices
from panelbench.hardware import published_variant

_SCHEMA_PREFIX = "panel-sim-definition/"


def is_definition_text(text: str) -> bool:
    """Whether *text* is a panel definition file of any schema version."""
    head = yaml.safe_load(text)
    return isinstance(head, dict) and str(head.get("schema", "")).startswith(_SCHEMA_PREFIX)


def config_from_definition_text(text: str) -> dict[str, object]:
    """``config_from_definition`` for a definition file's text.

    Read by upstream's own loader, which takes a path, so its exact-text rules for
    metadata apply rather than a re-implementation of them. The loader prefixes its
    errors with that path, a scratch file here, so they name the definition instead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "definition.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            definition = load_definition(path)
        except EmitterError as exc:
            raise ValueError(str(exc).replace(str(path), "the definition")) from exc
    return config_from_definition(definition)


def config_from_definition(definition: PanelDefinition) -> dict[str, object]:
    """A PanelBench config for the panel *definition* describes.

    The variant the definition declares outranks the one its panel's hardware version
    selects, and the config records it where the two differ, so a panel on a hardware
    string PanelBench has no variant for still publishes as its definition says. A
    variant PanelBench does not publish is refused rather than published as another.
    """
    try:
        variant = published_variant(definition.variant)
    except ValueError as exc:
        raise ValueError(f"The definition cannot be imported: {exc}") from exc
    panels = definition.manifest.of_class("panel")
    if len(panels) != 1:
        raise ValueError(f"a panel definition needs exactly one panel device, found {len(panels)}")
    if len(definition.bess_configs) > 1:
        raise ValueError(
            "PanelBench models one battery per panel; this definition has "
            f"{len(definition.bess_configs)}"
        )
    batteries = {i.instance_id for i in definition.manifest.of_class("bess")}
    for battery in definition.bess_configs:
        # Upstream builds a battery from its config alone, so the definition renders
        # either way; the dispatch settings would then describe no published device.
        if battery.instance_id not in batteries:
            raise ValueError(
                f"the definition's battery config {battery.instance_id!r} names no bess device"
            )
    config = translate_panel_tree(
        panels[0].instance_id, discovered_devices(_render(definition)), variant=variant
    )
    _drop_device_numbers(config)
    _overlay_emitter_options(config, definition)
    return config


def _drop_device_numbers(config: dict[str, object]) -> None:
    """Forget the device numbers the translation read from the rendered tree.

    A definition's ids are its own, masked and renumbered in space order where it
    was captured from a panel, so the numbers they carry are no panel's: an import
    publishes the hashed ids a config with none does.
    """
    for section in ("circuits", "outside_meters"):
        entries = config.get(section)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                entry.pop("circuit_number", None)
    panel = config.get("panel_config")
    if isinstance(panel, dict):
        panel.pop("device_id_hex", None)


def _render(definition: PanelDefinition) -> dict[str, bytes]:
    """The retained tree upstream's emitter publishes for *definition*."""
    transport = RecordingTransport()
    emitter = construct_emitter(definition, SetterRegistry(), transport)
    emitter.start()
    emitter.publish_tick(TickInputs(current_time=0.0, grid_online=True, circuits={}))
    return transport.retained


def _overlay_emitter_options(config: dict[str, object], definition: PanelDefinition) -> None:
    if definition.bess_configs:
        bess = config.get("bess")
        if not isinstance(bess, dict):
            raise ValueError(
                "the definition's battery did not survive translation, so importing it "
                "would silently drop the battery"
            )
        bess.update(bess_dispatch_yaml(definition.bess_configs[0], definition.manifest))
        # The definition says what the panel is, so its battery's coupling outranks the
        # one the clone reads from the tree's topology.
        if any(
            battery.metadata.get("inverter-type") == "hybrid"
            for battery in definition.manifest.of_class("bess")
        ):
            bess["inverter_type"] = "hybrid"
        else:
            bess.pop("inverter_type", None)
    if definition.load_shedding is not None:
        panel = config["panel_config"]
        if not isinstance(panel, dict):
            raise ValueError("the translated config has no panel_config mapping")
        panel.update(load_shedding_yaml(definition.load_shedding))
