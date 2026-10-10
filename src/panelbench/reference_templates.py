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

A template is named for the panel it is, not for its capture's handle, so the
dashboard's list reads as hardware (``_TEMPLATE_NAMES``).
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import yaml
from ebus_panel_sim import EmitterError, load_reference_capture, reference_capture_names

from panelbench.definition_import import config_from_definition
from panelbench.validation import validate_yaml_config

_LOGGER = logging.getLogger(__name__)


TEMPLATE_PREFIX = "default_reference_"
"""What names a reference capture's template: ``default_``, which the dashboard lists
as a read-only template, then a marker one ignore rule covers by convention, so no
generated template is tracked however a capture is named."""

_TEMPLATE_NAMES: Final[Mapping[str, str]] = {
    "main32_r202633": "MAIN_32_r202633",
    "main32_r202639": "MAIN_32_r202639",
    "r202639-a": "UNKNOWN_16_r202639",
    "r202639-b": "MAIN_16_r202639",
    "r202639-c": "MAIN_40_r202639",
    "r202639-d": "MLO_24_r202639",
    "r202639-e": "MLO_48_r202639",
}
"""What each reference capture's template is called, by the capture's handle: the model
its panel publishes, or ``UNKNOWN`` and its spaces where it publishes none, then its
firmware release, then whatever tells two captures of one model and release apart.
Written out rather than read from the capture, so publishing a capture again never
renames the template a ``--config`` names."""


def template_filename(name: str) -> str:
    """The template file for the reference capture *name*.

    A capture this release has no name for keeps its handle, so an emitter newer than
    the pinned one still brings its captures' templates with it.
    """
    return f"{TEMPLATE_PREFIX}{_TEMPLATE_NAMES.get(name, name)}.yaml"


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

    A template already there and current is left alone. The templates are optional,
    so one the emitter cannot import, or one the directory will not take (read-only,
    no permission, a full disk), is logged and skipped: neither keeps the others or
    the app from starting.
    """
    written: list[Path] = []
    for name in reference_capture_names():
        path = config_dir / template_filename(name)
        try:
            text = yaml.dump(reference_template(name), default_flow_style=False, sort_keys=False)
        except (EmitterError, ValueError) as exc:
            _LOGGER.error("Reference capture %s could not be made a template: %s", name, exc)
            continue
        try:
            if path.exists() and path.read_text(encoding="utf-8") == text:
                continue
            _replace(path, text)
        except OSError as exc:
            _LOGGER.warning("Reference capture template %s was not written: %s", path, exc)
            continue
        _LOGGER.info("Wrote reference capture template: %s", path.name)
        written.append(path)
    return written


def _replace(path: Path, text: str) -> None:
    """Write *text* to *path* whole, so a simulator starting beside another never reads
    half a file.

    Staged in a file of its own beside *path*, so simulators starting together never
    write one staging file, and removed again if the write fails.
    """
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as staged_file:
            staged_file.write(text)
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
