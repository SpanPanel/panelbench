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
several can run on one broker, and a later capture of a panel takes its earlier one's
(``_SUCCEEDS``), as a panel keeps its serial through a firmware upgrade.

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
from typing import TYPE_CHECKING, Final

import yaml
from ebus_panel_sim import EmitterError, load_reference_capture, reference_capture_names

from panelbench.definition_import import config_from_definition
from panelbench.device_numbers import hex_of, is_remote_ct, number_of
from panelbench.validation import validate_yaml_config

if TYPE_CHECKING:
    from ebus_panel_sim.capture import Tree

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

_SUCCEEDS: Final[Mapping[str, str]] = {
    "main32_r202645": "main32_r202639",
    "r202645-a": "r202639-a",
    "r202645-c": "r202639-c",
    "r202645-e": "r202639-e",
}
"""Each later capture of a panel, with the earlier capture of it whose template name
and serial it takes. A panel keeps its serial through a firmware upgrade, so a config
running the earlier capture, from a PanelBench release that had it, and one running
the later are one panel upgraded, as Home Assistant sees it."""

_RETIRED_CAPTURES: Final = frozenset({"main32_r202633", *_SUCCEEDS.values()})

_BEFORE_TEMPLATES: Final[Mapping[str, str]] = {
    "main32_r202639": "MAIN_32_R202639",
    "r202639-a": "UNKNOWN_16_R202639",
    "r202639-c": "MAIN_40_R202639",
    "r202639-e": "MLO_48_R202639",
}
"""PRE-RELEASE ONLY: strip this table before a production release.

The captures ``_SUCCEEDS`` retires, written again as templates named for their
release: each is its panel before the upgrade to release 202645, with the serial
its successor keeps, so running it and then its successor exercises a consumer's
migration across the upgrade. Only one of a pair runs at a time, as PanelBench
refuses a second panel with a running serial."""
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
    return tuple(
        n
        for n in reference_capture_names()
        if n not in _RETIRED_CAPTURES or n in _BEFORE_TEMPLATES
    )


def template_filename(name: str) -> str:
    """The template file for the reference capture *name*.

    A capture this release has no name for keeps its handle, so an emitter newer than
    the pinned one still brings its captures' templates with it.
    """
    named = _BEFORE_TEMPLATES.get(name) or _TEMPLATE_NAMES.get(name, name)
    return f"{TEMPLATE_PREFIX}{named}.yaml"


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
    capture = load_reference_capture(name)
    config = config_from_definition(capture.definition)
    panel = config["panel_config"]
    if not isinstance(panel, dict):
        raise ValueError(f"reference capture {name!r} imported with no panel_config")
    panel["serial_number"] = f"sim-{_SUCCEEDS.get(name, name)}"
    _take_device_numbers(name, config, panel, capture.tree)
    validate_yaml_config(config)
    return config


def _take_device_numbers(
    name: str, config: dict[str, object], panel: dict[str, object], tree: Tree
) -> None:
    """Number the template's circuits, its remote CT and its panel as the panel the
    capture *name* records names them, so the template publishes the ids it did.

    A definition's ids are masked and renumbered in space order, so the numbers come
    from the capture's tree: each circuit is matched by its spaces, and by its name
    where several share them, as a tandem's do; a remote CT, on no space, is the
    template's one meter outside the panel. A capture whose ids carry no numbers, a
    MAIN 32's or one masked whole, numbers nothing.

    Raises:
        ValueError: a numbered device of the tree matches no one entry of the config.
    """
    circuits = [c for c in _entries(config.get("circuits"))]
    meters = [m for m in _entries(config.get("outside_meters"))]
    for device_id, device in tree.items():
        number = number_of(device_id)
        if device.type != "circuit" or number is None:
            continue
        if is_remote_ct(device_id):
            matches = meters
        else:
            spaces = [int(s) for s in str(device.value("info/spaces") or "").split(",") if s]
            matches = [c for c in circuits if c.get("tabs") == spaces]
            if len(matches) > 1:
                matches = [c for c in matches if c.get("name") == device.value("info/name")]
        if len(matches) != 1:
            raise ValueError(
                f"reference capture {name!r} publishes {device_id}, which matches "
                f"{len(matches)} of its template's entries, not one"
            )
        matches[0]["circuit_number"] = number
    root = next(i for i, d in tree.items() if d.type == "distribution-enclosure")
    if (segment := hex_of(root, tree)) is not None:
        panel["device_id_hex"] = segment


def _entries(value: object) -> list[dict[str, object]]:
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


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
