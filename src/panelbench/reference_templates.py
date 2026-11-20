"""What a template is: the contract every shipped template shares.

A template is a config whose name starts with ``ref_`` (``TEMPLATE_PREFIX``), read-only
in the dashboard (``is_template``), opened first by ``first_template`` and cloned under
a name without the marker (``clone_name``). Two are hand-written and tracked in the
repository's ``configs/``; the rest are built here, one for each reference capture the
pinned emitter ships. The emitter's masked captures of real SPAN panels each come with
the panel definition recorded with them (``load_reference_capture``), so a user can run
a panel as a real one published itself.

A template is built, not kept: at every start the definition is read through
PanelBench's own definition import, as the dashboard's Import reads one, and written
into the config directory where it is missing or stale, as ``run.sh`` refreshes the
shipped templates. So no copy of a capture lives in this repository, and a new emitter
release brings its captures' templates with it. Each takes a serial of its own, so
several can run on one broker.

A template is named for the panel it is, not for its capture's handle, so the
dashboard's list reads as hardware (``_TEMPLATE_NAMES``). A capture the release has
retired gets no template (``_RETIRED_CAPTURES``).
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


TEMPLATE_PREFIX = "ref_"
"""What names a template: each reference capture's, and each hand-written one the
repository tracks. One ignore rule covers the marker, so no generated template is
tracked however a capture is named; the hand-written ones are excepted by name. The
dashboard keeps every file under it read-only (``is_template``)."""

_TEMPLATE_NAMES: Final[Mapping[str, str]] = {
    "main32_r202645": "MAIN_32",
    "main32_r202645-cascade": "MAIN_32_CASCADE",
    "main32_r202639-upstream-pv": "MAIN_32_UPSTREAM_PV",
    "r202645-a": "UNKNOWN_16",
    "r202639-b": "MAIN_16",
    "r202645-g": "MAIN_16_BESS",
    "r202645-c": "MAIN_40",
    "r202639-d": "MLO_24",
    "r202645-e": "MLO_48",
    "r202639-f": "MLO_48_HYBRID",
}
"""What each reference capture's template is called, by the capture's handle: the model
its panel publishes, or ``UNKNOWN`` and its spaces where it publishes none, then
whatever tells two captures of one model apart. The firmware release is not in the
name: the template carries it as ``firmware_version``. Written out rather than read
from the capture, so publishing a capture again never renames the template a
``--config`` names."""

_RETIRED_CAPTURES: Final = frozenset(
    {"main32_r202633", "main32_r202639", "r202639-a", "r202639-c", "r202639-e"}
)
"""Captures written as no template, whose names later captures take.

``main32_r202633`` is a MAIN 32 on a release before 202639, whose name would be
``MAIN_32`` as the later MAIN 32 capture's is. ``main32_r202639``, ``r202639-a``,
``r202639-c`` and ``r202639-e`` give their names to the r202645 captures of the same
panels, which keep their masked serials; ``r202645-c`` is that MAIN 40 recommissioned,
with 38 circuits and no battery."""


def is_template(filename: str) -> bool:
    """Whether *filename* is a template the dashboard keeps read-only: hand-written and
    tracked, or built from a reference capture."""
    return filename.startswith(TEMPLATE_PREFIX)


def template_capture_names() -> tuple[str, ...]:
    """The reference captures written as templates, in the emitter's order."""
    return tuple(n for n in reference_capture_names() if n not in _RETIRED_CAPTURES)


def template_filename(name: str) -> str:
    """The template file for the reference capture *name*.

    A capture this release has no name for keeps its handle, so an emitter newer than
    the pinned one still brings its captures' templates with it.
    """
    return f"{TEMPLATE_PREFIX}{_TEMPLATE_NAMES.get(name, name)}.yaml"


FIRST_TEMPLATE: Final = template_filename("main32_r202645")
"""The template the dashboard opens when it has no config to open: a real MAIN 32 on
release 202645, as it published itself."""


def first_template(config_dir: Path) -> Path | None:
    """The template the dashboard opens in *config_dir* when it has no config to open.

    ``FIRST_TEMPLATE`` where the directory holds it, else the first template there by
    name, as when the emitter could not import that capture or the directory refused
    the write, else none.
    """
    preferred = config_dir / FIRST_TEMPLATE
    if preferred.is_file():
        return preferred
    return next(iter(sorted(config_dir.glob(f"{TEMPLATE_PREFIX}*.yaml"))), None)


def clone_name(filename: str) -> str:
    """The name the dashboard offers for a clone of *filename*: a template's without its
    marker, so the clone is no template, and any other config's as it is."""
    return filename.removeprefix(TEMPLATE_PREFIX)


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
    for name in template_capture_names():
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
