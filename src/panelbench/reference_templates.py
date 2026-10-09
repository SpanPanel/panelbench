"""A template for each reference capture the pinned emitter ships.

The emitter ships masked captures of real SPAN panels, each with the panel definition
recorded with it (``load_reference_capture``). Each becomes a ``default_*`` template,
read-only in the dashboard and cloned like the shipped ones, so a user can run a panel
as a real one published itself.

A template is built, not kept: at every start the definition is read through
PanelBench's own definition import, as the dashboard's Import reads one, and written
into the config directory where it is missing or stale, as ``run.sh`` refreshes the
shipped templates. So no copy of a capture lives in this repository, and a new emitter
release brings its captures' templates with it. Each takes a serial of its own, so
several can run on one broker.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml
from ebus_panel_sim import EmitterError, load_reference_capture, reference_capture_names

from panelbench.definition_import import config_from_definition
from panelbench.validation import validate_yaml_config

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def template_filename(name: str) -> str:
    """The template file for the reference capture *name*."""
    return f"default_{name}.yaml"


def reference_template(name: str) -> dict[str, object]:
    """The config for the reference capture *name*, one of ``reference_capture_names()``."""
    config = config_from_definition(load_reference_capture(name).definition)
    panel = config["panel_config"]
    if not isinstance(panel, dict):
        raise ValueError(f"reference capture {name!r} imported with no panel_config")
    panel["serial_number"] = f"sim-{name}"
    validate_yaml_config(config)
    return config


def write_reference_templates(config_dir: Path) -> list[Path]:
    """Lay each reference capture's template into *config_dir*; return those written.

    A template already there and current is left alone. One the emitter cannot import
    is logged and skipped, so one capture never keeps the others or the app from
    starting.
    """
    written: list[Path] = []
    for name in reference_capture_names():
        path = config_dir / template_filename(name)
        try:
            text = yaml.dump(reference_template(name), default_flow_style=False, sort_keys=False)
        except (EmitterError, ValueError) as exc:
            _LOGGER.error("Reference capture %s could not be made a template: %s", name, exc)
            continue
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.write_text(text, encoding="utf-8")
        _LOGGER.info("Wrote reference capture template: %s", path.name)
        written.append(path)
    return written
