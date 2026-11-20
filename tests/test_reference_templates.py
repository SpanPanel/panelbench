"""Each reference capture is a template a user can run, built from the pinned emitter.

The templates are written into the config directory at every start
(``reference_templates``), so the tests write them into a temporary one. Each is
loaded as the dashboard loads a template, and run at the captured instant, where it
reproduces its capture.
"""

from __future__ import annotations

import fnmatch
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, cast

import pytest
import yaml
from ebus_panel_sim import load_reference_capture, reference_capture_names

from panelbench import __main__ as panelbench_main
from panelbench.config_defaults import normalize_config
from panelbench.dashboard.config_store import ConfigStore
from panelbench.emitter_adapter.spec_generator import build_manifest
from panelbench.reference_templates import (
    FIRST_TEMPLATE,
    TEMPLATE_PREFIX,
    clone_name,
    first_template,
    is_template,
    reference_template,
    template_capture_names,
    template_filename,
    write_reference_templates,
)
from tests._helpers import CONFIGS, SHIPPED_TEMPLATES, lay_every_template
from tests.fidelity.reproduction import assert_reproduces

if TYPE_CHECKING:
    from panelbench.config_types import SimulationConfig


def test_every_reference_capture_is_written_as_a_template(tmp_path: Path) -> None:
    written = write_reference_templates(tmp_path)

    names = template_capture_names()
    assert names
    assert sorted(path.name for path in written) == sorted(template_filename(n) for n in names)
    assert all(is_template(path.name) for path in written)


# Each capture retired for a later one of the same panel, that later capture, and the
# name it takes.
_SUCCESSORS: Final = {
    "main32_r202639": ("main32_r202645", "ref_MAIN_32.yaml"),
    "r202639-a": ("r202645-a", "ref_UNKNOWN_16.yaml"),
    "r202639-c": ("r202645-c", "ref_MAIN_40.yaml"),
    "r202639-e": ("r202645-e", "ref_MLO_48.yaml"),
}


def _panel_serial(name: str) -> str:
    """The masked serial *name*'s panel publishes, which it keeps from one capture to
    the next."""
    tree = load_reference_capture(name).tree
    (panel,) = [d for d in tree.values() if d.type == "distribution-enclosure"]
    return str(panel.value("info/serial-number"))


def test_a_retired_capture_is_written_as_no_template(tmp_path: Path) -> None:
    """A capture of a panel the emitter also ships a later capture of gets no template:
    the later capture of the same panel, which keeps its masked serial, takes its name.
    ``main32_r202633``, masked before panels kept theirs, is a MAIN 32 on a release
    before 202639, whose name the later MAIN 32 capture takes."""
    write_reference_templates(tmp_path)

    assert "main32_r202633" in reference_capture_names()
    assert "main32_r202633" not in template_capture_names()
    for retired, (successor, filename) in _SUCCESSORS.items():
        assert retired in reference_capture_names(), retired
        assert retired not in template_capture_names(), retired
        assert _panel_serial(retired) == _panel_serial(successor), retired
        written = yaml.safe_load((tmp_path / filename).read_text(encoding="utf-8"))
        # A panel keeps its serial through the upgrade, so the template keeps the
        # serial it had while it ran the earlier capture.
        assert written["panel_config"]["serial_number"] == f"sim-{retired}", retired


@pytest.mark.parametrize("name", template_capture_names())
def test_a_template_is_named_for_the_panel_its_capture_publishes(name: str) -> None:
    """By the model the panel publishes, or ``UNKNOWN`` and its spaces where it publishes
    none: a capture's handle says neither. The firmware release is the template's
    ``firmware_version``, not part of its name. A name may go on to tell two captures of
    one model apart."""
    config = reference_template(name)
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    model = panel["model"]
    if model == "UNKNOWN":
        model = f"UNKNOWN_{panel['total_tabs']}"

    named = template_filename(name).removeprefix(TEMPLATE_PREFIX).removesuffix(".yaml")

    assert named == model or named.startswith(f"{model}_")
    assert str(config["firmware_version"]).split("/")[1] not in named


def test_no_two_reference_captures_share_a_template() -> None:
    """Each is written under its own name, so none overwrites another's template."""
    filenames = [template_filename(n) for n in template_capture_names()]

    assert len(set(filenames)) == len(filenames)


def test_a_capture_with_no_name_here_keeps_its_handle() -> None:
    """An emitter newer than the pinned one still brings its captures' templates."""
    assert template_filename("a-later-capture") == f"{TEMPLATE_PREFIX}a-later-capture.yaml"


def test_a_current_template_is_left_alone_and_a_stale_one_refreshed(tmp_path: Path) -> None:
    write_reference_templates(tmp_path)
    [first, *_rest] = template_capture_names()
    stale = tmp_path / template_filename(first)
    stale.write_text("panel_config:\n  serial_number: stale\n", encoding="utf-8")
    users = tmp_path / "my-panel.yaml"
    users.write_text("mine\n", encoding="utf-8")

    rewritten = write_reference_templates(tmp_path)

    assert rewritten == [stale]
    assert users.read_text(encoding="utf-8") == "mine\n"


def test_each_template_runs_on_a_serial_of_its_own(tmp_path: Path) -> None:
    """So several can run on one broker without claiming each other's topics."""
    write_reference_templates(tmp_path)

    serials = [
        yaml.safe_load((tmp_path / template_filename(n)).read_text(encoding="utf-8"))[
            "panel_config"
        ]["serial_number"]
        for n in template_capture_names()
    ]

    assert len(set(serials)) == len(serials)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", template_capture_names())
async def test_the_template_loads_and_reproduces_its_capture(name: str, tmp_path: Path) -> None:
    write_reference_templates(tmp_path)
    path = tmp_path / template_filename(name)
    ConfigStore().load_from_file(path)

    await assert_reproduces(path, load_reference_capture(name), exactly=True)


class _ConfigsAtStart:
    """Stands in for the app, recording which configs the directory held when it ran."""

    seen: ClassVar[list[str]] = []

    def __init__(self, *, config_dir: Path, **_options: object) -> None:
        self._config_dir = config_dir

    async def run(self) -> None:
        _ConfigsAtStart.seen = sorted(p.name for p in self._config_dir.glob("*.yaml"))

    async def stop(self) -> None:
        return None


def test_the_templates_are_there_before_the_app_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(panelbench_main, "SimulatorApp", _ConfigsAtStart)

    panelbench_main.main(["--config-dir", str(tmp_path)])

    assert _ConfigsAtStart.seen == sorted(template_filename(n) for n in template_capture_names())


def test_a_config_dir_it_cannot_write_is_logged_and_startup_goes_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The templates are optional: a read-only directory skips them, it never stops a start."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="panelbench.reference_templates"):
            written = write_reference_templates(locked)
    finally:
        locked.chmod(0o700)

    assert written == []
    warned = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "panelbench.reference_templates"
    ]
    assert len(warned) == len(template_capture_names())
    assert all(str(locked) in message for message in warned)


def test_each_write_stages_in_a_file_of_its_own(tmp_path: Path) -> None:
    """Simulators starting together never share a staging file: one that holds the old
    fixed staging name stops nothing, and no staging file is left behind."""
    [name, *_rest] = template_capture_names()
    squatter = tmp_path / f".{template_filename(name)}.tmp"
    squatter.mkdir()

    written = write_reference_templates(tmp_path)

    assert tmp_path / template_filename(name) in written
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == [squatter.name]


def test_every_template_carries_the_marker_one_ignore_rule_covers() -> None:
    """Built at every start, never tracked: one rule in .gitignore covers each template
    by its marker, whatever a future capture is called, and the dashboard keeps it
    read-only by the same marker."""
    rule = "configs/ref_*.yaml"
    ignored = (Path(__file__).parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert rule in ignored
    for name in template_capture_names():
        filename = template_filename(name)
        assert fnmatch.fnmatch(f"configs/{filename}", rule)
        assert is_template(filename)


def test_only_a_ref_file_is_a_template() -> None:
    """Tracked or generated, a template carries the marker. A ``default_`` file an
    earlier release laid down is the user's now, as any other config is."""
    assert is_template("ref_MAIN_32_TANDEMS.yaml")
    assert is_template("ref_MAIN_32.yaml")
    assert is_template("default_MAIN_32.yaml") is False
    assert not is_template("my-panel.yaml")
    assert not is_template("main32_issue269.yaml")


@pytest.mark.parametrize(
    ("filename", "clone"),
    [
        ("ref_MAIN_32.yaml", "MAIN_32.yaml"),
        ("ref_MLO_48_TANDEMS.yaml", "MLO_48_TANDEMS.yaml"),
        ("default_MAIN_32.yaml", "default_MAIN_32.yaml"),
        ("my-panel.yaml", "my-panel.yaml"),
    ],
)
def test_a_clone_of_a_template_is_named_without_its_marker(filename: str, clone: str) -> None:
    """So the clone is no template; any other config's clone starts from its own name."""
    assert clone_name(filename) == clone
    assert not is_template(clone)


def test_the_first_template_is_ref_main_32(tmp_path: Path) -> None:
    """The one the dashboard opens with no config to open: a real MAIN 32 as it
    published itself, among every template a start lays down."""
    lay_every_template(tmp_path)

    assert FIRST_TEMPLATE == "ref_MAIN_32.yaml"
    assert first_template(tmp_path) == tmp_path / "ref_MAIN_32.yaml"


def test_without_ref_main_32_the_first_template_is_the_first_ref_one(tmp_path: Path) -> None:
    """As when the emitter imported no capture or the directory refused the writes: the
    first ``ref_`` file by name, never a user's clone or a ``default_`` file an earlier
    release left, though both sort before it."""
    for name in (*SHIPPED_TEMPLATES, "default_MAIN_16.yaml", "MAIN_32.yaml"):
        (tmp_path / name).write_text("panel_config: {}\n", encoding="utf-8")

    assert first_template(tmp_path) == tmp_path / "ref_MAIN_32_TANDEMS.yaml"


def test_with_no_template_there_is_no_first_one(tmp_path: Path) -> None:
    for name in ("default_MAIN_16.yaml", "MAIN_32.yaml"):
        (tmp_path / name).write_text("panel_config: {}\n", encoding="utf-8")

    assert first_template(tmp_path) is None


@pytest.mark.parametrize("template", SHIPPED_TEMPLATES)
def test_the_shipped_ref_templates_are_tracked(template: str) -> None:
    """The hand-written ``ref_`` templates sit beside the generated ones, which the
    ``ref_`` rule ignores: git must not ignore them."""
    path = CONFIGS / template

    assert path.is_file()
    # --no-index evaluates the rules whether or not the file is tracked: 0 is ignored,
    # 1 not ignored, 128 a git error.
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", str(path)], cwd=CONFIGS.parent, check=False
    )
    assert ignored.returncode == 1


def test_a_generated_template_never_takes_a_shipped_name() -> None:
    generated = {template_filename(n) for n in template_capture_names()}

    assert generated.isdisjoint(SHIPPED_TEMPLATES)


# The hardware 3.0 captures whose trees carry their panels' own device ids: every one
# but `r202639-f`, captured before captures kept them, whose ids are all masked.
_NUMBERED_CAPTURES: Final = tuple(
    name
    for name in template_capture_names()
    if load_reference_capture(name).definition.variant == "span-alpha-test-b2"
    and name != "r202639-f"
)


@pytest.mark.parametrize("name", _NUMBERED_CAPTURES)
def test_a_hardware_3_template_publishes_its_capture_s_device_ids(name: str) -> None:
    """Its circuits, remote CT and lugs under the ids its panel published, the panel's
    root id replaced by the template's serial."""
    tree = load_reference_capture(name).tree
    (root,) = [i for i, d in tree.items() if d.type == "distribution-enclosure"]
    config = cast("SimulationConfig", reference_template(name))
    serial = config["panel_config"]["serial_number"]
    normalize_config(config, source="test")

    manifest = build_manifest(config)

    published = {
        device.instance_id
        for kind in ("circuit", "remote-ct", "lugs")
        for device in manifest.of_class(kind)
    }
    expected = {
        i.replace(root, serial, 1) for i, d in tree.items() if d.type in ("circuit", "lugs")
    }
    assert published == expected
