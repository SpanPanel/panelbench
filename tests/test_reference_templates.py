"""Each reference capture is a template a user can run, built from the pinned emitter.

The templates are written into the config directory at every start
(``reference_templates``), so the tests write them into a temporary one. Each is
loaded as the dashboard loads a template, and run at the captured instant, where it
reproduces its capture.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

import pytest
import yaml
from ebus_panel_sim import load_reference_capture, reference_capture_names

from panelbench import __main__ as panelbench_main
from panelbench.dashboard.config_store import ConfigStore
from panelbench.reference_templates import template_filename, write_reference_templates
from tests.fidelity.reproduction import assert_reproduces

if TYPE_CHECKING:
    from pathlib import Path


def test_every_reference_capture_is_written_as_a_template(tmp_path: Path) -> None:
    written = write_reference_templates(tmp_path)

    names = reference_capture_names()
    assert names
    assert sorted(path.name for path in written) == sorted(template_filename(n) for n in names)
    assert all(path.name.startswith("default_") for path in written)


def test_a_current_template_is_left_alone_and_a_stale_one_refreshed(tmp_path: Path) -> None:
    write_reference_templates(tmp_path)
    [first, *_rest] = reference_capture_names()
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
        for n in reference_capture_names()
    ]

    assert len(set(serials)) == len(serials)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", reference_capture_names())
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

    assert _ConfigsAtStart.seen == sorted(template_filename(n) for n in reference_capture_names())


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
    assert len(warned) == len(reference_capture_names())
    assert all(str(locked) in message for message in warned)
