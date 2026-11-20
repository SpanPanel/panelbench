"""Tests for how the add-on's run.sh lays the shipped configs into its config directory.

As in ``test_run_sh_advertise_address.py``, the function under test is extracted
from the shipped ``run.sh`` and executed rather than restated here.

The shipped ``ref_*`` templates belong to PanelBench: the dashboard refuses to save,
delete or rename them. Seeding them only where they were missing meant an upgraded
add-on kept the copy the previous release laid down, so a template that had since
gained a ``firmware_version`` went on publishing whatever the old copy named. Every
other file in the directory is the user's and must survive a start untouched, a
``default_*`` template an earlier release laid down among them.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tests._helpers import CONFIGS, SHIPPED_TEMPLATES

_RUN_SH = Path(__file__).parent.parent / "panelbench" / "run.sh"

_CURRENT = "firmware_version: spanos3/r202639/03\n"
_STALE = "panel_config:\n  serial_number: sim-32\n"
# A shipped template, by name; each test gives it the content it needs.
_TEMPLATE = SHIPPED_TEMPLATES[0]


def _seed(shipped: Path, config_dir: Path) -> str:
    """Run run.sh's ``seed_configs`` from *shipped* into *config_dir* and return its log."""
    source = _RUN_SH.read_text()
    match = re.search(r"^seed_configs\(\) \{.*?^\}", source, re.MULTILINE | re.DOTALL)
    assert match, "run.sh no longer defines seed_configs()"

    # Mirrors run.sh: the same `set` flags, then the function and its call.
    script = f'set -euo pipefail\n{match.group(0)}\nseed_configs "$1" "$2"\n'
    result = subprocess.run(
        ["bash", "-c", script, "run.sh", str(shipped), str(config_dir)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
    )
    return result.stdout


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    shipped = tmp_path / "app-configs"
    config_dir = tmp_path / "config-panelbench"
    shipped.mkdir()
    config_dir.mkdir()
    return shipped, config_dir


def test_a_missing_template_is_seeded(tmp_path: Path) -> None:
    shipped, config_dir = _dirs(tmp_path)
    (shipped / _TEMPLATE).write_text(_CURRENT)

    log = _seed(shipped, config_dir)

    assert (config_dir / _TEMPLATE).read_text() == _CURRENT
    assert f"Seeded config: {_TEMPLATE}" in log


def test_a_stale_template_is_refreshed_and_logged(tmp_path: Path) -> None:
    """The upgrade defect: an older release's copy must not outlive the release."""
    shipped, config_dir = _dirs(tmp_path)
    (shipped / _TEMPLATE).write_text(_CURRENT)
    (config_dir / _TEMPLATE).write_text(_STALE)

    log = _seed(shipped, config_dir)

    assert (config_dir / _TEMPLATE).read_text() == _CURRENT
    assert f"Refreshed shipped template: {_TEMPLATE}" in log


def test_an_up_to_date_template_is_left_alone_and_not_logged(tmp_path: Path) -> None:
    """Nothing changed, so nothing is rewritten and the log stays quiet."""
    shipped, config_dir = _dirs(tmp_path)
    (shipped / _TEMPLATE).write_text(_CURRENT)
    (config_dir / _TEMPLATE).write_text(_CURRENT)

    log = _seed(shipped, config_dir)

    assert (config_dir / _TEMPLATE).read_text() == _CURRENT
    assert log == ""


def test_user_files_are_never_touched(tmp_path: Path) -> None:
    """A clone is the user's, even when it was cloned from a stale template. A ``ref_``
    template the image does not ship is one a start built, so it is the start's to
    write, not this function's."""
    shipped, config_dir = _dirs(tmp_path)
    (shipped / _TEMPLATE).write_text(_CURRENT)
    clone = config_dir / "MAIN_32_TANDEMS.yaml"
    clone.write_text(_STALE)
    generated = config_dir / "ref_MAIN_40.yaml"
    generated.write_text(_STALE)

    _seed(shipped, config_dir)

    assert clone.read_text() == _STALE
    assert generated.read_text() == _STALE


def test_a_default_template_an_earlier_release_laid_down_is_left_as_it_is(
    tmp_path: Path,
) -> None:
    """The image ships no ``default_*`` template now, so one an earlier release left is
    the user's: never refreshed, never removed, never logged."""
    config_dir = tmp_path / "config-panelbench"
    config_dir.mkdir()
    left = config_dir / "default_MAIN_40.yaml"
    left.write_text(_STALE)

    log = _seed(CONFIGS, config_dir)

    assert left.read_text() == _STALE
    assert "default_MAIN_40.yaml" not in log


def test_every_shipped_template_is_laid_down_and_a_stale_one_refreshed(tmp_path: Path) -> None:
    """From ``configs/``, as the add-on image ships it (``COPY configs/ configs/``): each
    tracked ``ref_`` template is laid down, and each one an earlier release left is
    refreshed. Named, not globbed: a checkout's ``configs/`` also holds the generated
    templates, which the image, built in CI from git, does not."""
    config_dir = tmp_path / "config-panelbench"
    config_dir.mkdir()

    _seed(CONFIGS, config_dir)

    for name in SHIPPED_TEMPLATES:
        assert (config_dir / name).read_bytes() == (CONFIGS / name).read_bytes()
        (config_dir / name).write_text(_STALE)

    log = _seed(CONFIGS, config_dir)

    for name in SHIPPED_TEMPLATES:
        assert (config_dir / name).read_bytes() == (CONFIGS / name).read_bytes()
        assert f"Refreshed shipped template: {name}" in log


def test_an_edited_shipped_non_template_is_kept(tmp_path: Path) -> None:
    """Only ``ref_*`` is PanelBench's to refresh; any other shipped file is seeded once."""
    shipped, config_dir = _dirs(tmp_path)
    (shipped / "example.yaml").write_text(_CURRENT)
    edited = config_dir / "example.yaml"
    edited.write_text(_STALE)

    log = _seed(shipped, config_dir)

    assert edited.read_text() == _STALE
    assert log == ""
