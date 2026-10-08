"""What it takes to reach a source panel is kept out of the configs that clone it.

A clone config is a file users export, share and paste into issues. The panel's
passphrase, and the broker credentials registering with it returns, are kept in a
store of their own, keyed by the source panel's serial, readable only by its owner.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from panelbench import panel_secrets
from panelbench.app import SimulatorApp
from panelbench.panel_secrets import (
    SECRETS_FILENAME,
    BrokerCredentials,
    PanelSecrets,
    PanelSecretsStore,
    PanelSecretsUnreadable,
)

_SERIAL = "example-panel-001"
_BROKER = BrokerCredentials(
    username="example-user",
    password="example-broker-password",
    port=8883,
    ca_pem="-----BEGIN CERTIFICATE-----\nexample\n-----END CERTIFICATE-----\n",
)
_REPO = Path(__file__).resolve().parents[1]


def _store(tmp_path: Path) -> PanelSecretsStore:
    return PanelSecretsStore(tmp_path / "secrets" / "panel_sources.json")


def _legacy_clone(passphrase: object = "example-passphrase") -> dict[str, object]:
    """A clone written before the store existed, carrying its passphrase inline."""
    return {
        "panel_config": {"serial_number": f"sim-{_SERIAL}-clone"},
        "panel_source": {
            "origin_serial": _SERIAL,
            "host": "192.0.2.10",
            "passphrase": passphrase,
            "last_synced": "2026-01-01T00:00:00+00:00",
        },
    }


def test_what_is_remembered_comes_back_by_serial(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.remember(_SERIAL, passphrase="example-passphrase", broker=_BROKER)

    assert store.get(_SERIAL) == PanelSecrets(passphrase="example-passphrase", broker=_BROKER)
    assert store.get("another-panel") == PanelSecrets()


def test_remembering_one_secret_keeps_the_other(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.remember(_SERIAL, passphrase="example-passphrase")

    store.remember(_SERIAL, broker=_BROKER)

    assert store.get(_SERIAL) == PanelSecrets(passphrase="example-passphrase", broker=_BROKER)


def test_a_second_store_on_the_same_file_reads_it(tmp_path: Path) -> None:
    """What a restart sees: the store is the file, not the object."""
    _store(tmp_path).remember(_SERIAL, broker=_BROKER)

    assert _store(tmp_path).get(_SERIAL).broker == _BROKER


def test_only_the_owner_can_read_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.remember(_SERIAL, passphrase="example-passphrase")

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_a_passphrase_in_a_config_moves_into_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = _legacy_clone()

    assert store.take_from_config(config) is True

    panel_source = config["panel_source"]
    assert isinstance(panel_source, dict)
    assert "passphrase" not in panel_source
    assert panel_source["origin_serial"] == _SERIAL
    assert store.get(_SERIAL).passphrase == "example-passphrase"
    assert store.take_from_config(config) is False


def test_a_null_passphrase_is_dropped_without_a_trace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = _legacy_clone(passphrase=None)

    assert store.take_from_config(config) is True

    assert store.get(_SERIAL) == PanelSecrets()
    assert not store.path.exists()


def test_the_store_keeps_its_own_passphrase_over_a_configs(tmp_path: Path) -> None:
    """The store is written by this release; a config's copy is older than it."""
    store = _store(tmp_path)
    store.remember(_SERIAL, passphrase="current-passphrase")

    store.take_from_config(_legacy_clone(passphrase="stale-passphrase"))

    assert store.get(_SERIAL).passphrase == "current-passphrase"


def test_migrating_a_config_directory_rewrites_only_what_carries_a_passphrase(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    configs = tmp_path / "configs"
    configs.mkdir()
    legacy = configs / "legacy-clone.yaml"
    legacy.write_text(yaml.safe_dump(_legacy_clone(), sort_keys=False), encoding="utf-8")
    plain = configs / "plain.yaml"
    plain_text = "# a comment the migration must not lose\npanel_config: {serial_number: x}\n"
    plain.write_text(plain_text, encoding="utf-8")

    migrated = store.migrate_config_files(configs)

    assert migrated == [legacy]
    assert "passphrase" not in legacy.read_text(encoding="utf-8")
    assert yaml.safe_load(legacy.read_text(encoding="utf-8"))["panel_source"]["host"] == (
        "192.0.2.10"
    )
    assert plain.read_text(encoding="utf-8") == plain_text
    assert store.get(_SERIAL).passphrase == "example-passphrase"


@pytest.mark.asyncio
async def test_every_scan_of_the_config_directory_moves_passphrases_out(tmp_path: Path) -> None:
    """Startup's scan included, so a clone carrying one gives it up before it runs."""
    configs = tmp_path / "configs"
    configs.mkdir()
    legacy = configs / "legacy-clone.yaml"
    legacy.write_text(yaml.safe_dump(_legacy_clone(), sort_keys=False), encoding="utf-8")
    app = SimulatorApp(config_dir=configs, config_filter="", secrets_dir=tmp_path / "secrets")

    await app.reload()

    assert "passphrase" not in legacy.read_text(encoding="utf-8")
    store = PanelSecretsStore(tmp_path / "secrets" / SECRETS_FILENAME)
    assert store.get(_SERIAL).passphrase == "example-passphrase"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_default_store_in_a_checkout_is_gitignored() -> None:
    default = PanelSecretsStore.in_config_dir(_REPO / "configs").path
    result = subprocess.run(
        ["git", "-C", str(_REPO), "check-ignore", "-q", str(default)],
        check=False,
    )
    if result.returncode == 128:
        pytest.skip("not a git checkout")

    assert result.returncode == 0, f"{default} is not gitignored"


# -- a store that cannot be read, and configs that cannot be migrated ------------


def test_a_corrupt_store_is_refused_and_kept(tmp_path: Path) -> None:
    """Overwriting it with one new entry would lose every other panel's secrets, and
    re-registering is then the only way back to them."""
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(PanelSecretsUnreadable, match=str(store.path)):
        store.get(_SERIAL)
    with pytest.raises(PanelSecretsUnreadable):
        store.remember(_SERIAL, passphrase="example-passphrase")

    assert store.path.read_text(encoding="utf-8") == "{ not json"


def test_a_store_with_a_malformed_entry_is_refused(tmp_path: Path) -> None:
    """Skipping the entry would drop it from the file at the next write."""
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps({_SERIAL: {"broker": {"username": 1}}}), encoding="utf-8")

    with pytest.raises(PanelSecretsUnreadable):
        store.get("another-panel")


def test_a_write_reaches_the_disk_before_it_replaces_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A power loss after the rename must not leave an empty file in its place."""
    store = _store(tmp_path)
    calls: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        calls.append("fsync")
        real_fsync(fd)

    def replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(panel_secrets.os, "fsync", fsync)
    monkeypatch.setattr(panel_secrets.os, "replace", replace)

    store.remember(_SERIAL, passphrase="example-passphrase")

    assert calls[:2] == ["fsync", "replace"]


def test_one_unmigratable_config_does_not_stop_the_rest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A config that is not UTF-8 is reported by name and left; the others migrate."""
    store = _store(tmp_path)
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "a-unreadable.yaml").write_bytes(b"passphrase: \xff\xfe\n")
    legacy = configs / "b-legacy-clone.yaml"
    legacy.write_text(yaml.safe_dump(_legacy_clone(), sort_keys=False), encoding="utf-8")

    migrated = store.migrate_config_files(configs)

    assert migrated == [legacy]
    assert "a-unreadable.yaml" in caplog.text


def test_an_unwritable_store_leaves_the_config_as_it_was(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The passphrase stays where it is rather than being dropped with nowhere to go."""
    # Its directory cannot be made: a file stands where it would go, as a full or
    # read-only /data would refuse it.
    (tmp_path / "secrets").write_text("", encoding="utf-8")
    store = _store(tmp_path)
    configs = tmp_path / "configs"
    configs.mkdir()
    legacy = configs / "legacy-clone.yaml"
    text = yaml.safe_dump(_legacy_clone(), sort_keys=False)
    legacy.write_text(text, encoding="utf-8")

    migrated = store.migrate_config_files(configs)

    assert migrated == []
    assert legacy.read_text(encoding="utf-8") == text
    assert "legacy-clone.yaml" in caplog.text


@pytest.mark.asyncio
async def test_a_corrupt_store_does_not_stop_a_reload(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "legacy-clone.yaml").write_text(
        yaml.safe_dump(_legacy_clone(), sort_keys=False), encoding="utf-8"
    )
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / SECRETS_FILENAME).write_text("{ not json", encoding="utf-8")
    app = SimulatorApp(config_dir=configs, config_filter="", secrets_dir=secrets_dir)

    await app.reload()

    assert (secrets_dir / SECRETS_FILENAME).read_text(encoding="utf-8") == "{ not json"
