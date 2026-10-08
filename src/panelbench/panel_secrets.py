"""What it takes to reach a source panel, kept out of the configs that clone it.

A clone config is a file users export, share and paste into issues. The source
panel's passphrase, and the broker credentials that registering with it returns,
are not part of what is simulated and do not belong in it. They are kept here
instead, keyed by the source panel's serial, in a file only its owner can read.

The passphrase used to be written into the clone's ``panel_source``. A config that
still carries one gives it up the first time it is loaded: it moves into this store
and the config is never written with it again.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml

_LOGGER = logging.getLogger(__name__)

SECRETS_DIRNAME = ".secrets"
SECRETS_FILENAME = "panel_sources.json"

_PASSPHRASE = "passphrase"


@dataclass(frozen=True, slots=True)
class BrokerCredentials:
    """How to reach a panel's MQTTS broker: what registering returned, or a user had.

    No host: the broker runs on the panel, and is dialled wherever the panel is
    reached, not at a name kept from when these were issued.
    """

    username: str
    password: str
    port: int
    ca_pem: str


@dataclass(frozen=True, slots=True)
class PanelSecrets:
    """Everything kept for one source panel. Either may be unknown."""

    passphrase: str | None = None
    broker: BrokerCredentials | None = None


class PanelSecretsStore:
    """The secrets of every source panel, in one JSON file keyed by serial.

    Read from the file on every access rather than cached, so the dashboard and the
    app may each hold one on the same file and never disagree.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def in_config_dir(cls, config_dir: Path) -> PanelSecretsStore:
        """The store beside a config directory, where no config scan reads it."""
        return cls(config_dir / SECRETS_DIRNAME / SECRETS_FILENAME)

    def get(self, origin_serial: str) -> PanelSecrets:
        return self._read().get(origin_serial, PanelSecrets())

    def remember(
        self,
        origin_serial: str,
        *,
        passphrase: str | None = None,
        broker: BrokerCredentials | None = None,
    ) -> None:
        """Record what is given for *origin_serial*, keeping whatever is not."""
        if passphrase is None and broker is None:
            return
        entries = self._read()
        current = entries.get(origin_serial, PanelSecrets())
        updated = replace(
            current,
            passphrase=passphrase if passphrase is not None else current.passphrase,
            broker=broker if broker is not None else current.broker,
        )
        if updated == current:
            return
        entries[origin_serial] = updated
        self._write(entries)

    def take_from_config(self, config: MutableMapping[str, object]) -> bool:
        """Move a passphrase out of *config*'s ``panel_source`` into this store.

        The store keeps a passphrase it already holds: it is written by this release,
        and a config's copy is older. Answers whether *config* changed.
        """
        panel_source = config.get("panel_source")
        if not isinstance(panel_source, dict) or _PASSPHRASE not in panel_source:
            return False
        passphrase = panel_source.pop(_PASSPHRASE)
        origin_serial = panel_source.get("origin_serial")
        if (
            isinstance(passphrase, str)
            and passphrase
            and isinstance(origin_serial, str)
            and self.get(origin_serial).passphrase is None
        ):
            self.remember(origin_serial, passphrase=passphrase)
        return True

    def migrate_config_files(self, config_dir: Path) -> list[Path]:
        """Take the passphrase out of every config in *config_dir* that carries one.

        A file is rewritten only when it carries one, so every other config keeps
        its text, comments and all. Answers the files rewritten.
        """
        migrated: list[Path] = []
        for pattern in ("*.yaml", "*.yml"):
            for path in sorted(config_dir.glob(pattern)):
                text = path.read_text(encoding="utf-8")
                if _PASSPHRASE not in text:
                    continue
                try:
                    config = yaml.safe_load(text)
                except yaml.YAMLError:
                    continue
                if not isinstance(config, dict) or not self.take_from_config(config):
                    continue
                rewritten = yaml.dump(
                    config, default_flow_style=False, sort_keys=False, allow_unicode=True
                )
                path.write_text(rewritten, encoding="utf-8")
                _LOGGER.info("Moved the source panel's passphrase out of %s", path.name)
                migrated.append(path)
        return migrated

    # -- the file -----------------------------------------------------------------

    def _read(self) -> dict[str, PanelSecrets]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Ignoring the unreadable panel secrets file %s: %s", self.path, exc)
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            serial: secrets
            for serial, entry in raw.items()
            if isinstance(serial, str) and (secrets := _parse_entry(entry)) is not None
        }

    def _write(self, entries: dict[str, PanelSecrets]) -> None:
        """Replace the file in one step, readable and writable only by its owner."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        payload = json.dumps(
            {serial: _entry(secrets) for serial, secrets in sorted(entries.items())}, indent=2
        )
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".panel_sources.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def _entry(secrets: PanelSecrets) -> dict[str, object]:
    entry: dict[str, object] = {}
    if secrets.passphrase is not None:
        entry["passphrase"] = secrets.passphrase
    if secrets.broker is not None:
        entry["broker"] = asdict(secrets.broker)
    return entry


def _parse_entry(entry: object) -> PanelSecrets | None:
    if not isinstance(entry, dict):
        return None
    passphrase = entry.get("passphrase")
    return PanelSecrets(
        passphrase=passphrase if isinstance(passphrase, str) else None,
        broker=_parse_broker(entry.get("broker")),
    )


def _parse_broker(raw: object) -> BrokerCredentials | None:
    if not isinstance(raw, dict):
        return None
    username, password, port, ca_pem = (
        raw.get(key) for key in ("username", "password", "port", "ca_pem")
    )
    if not (
        isinstance(username, str)
        and isinstance(password, str)
        and isinstance(port, int)
        and isinstance(ca_pem, str)
    ):
        return None
    return BrokerCredentials(username=username, password=password, port=port, ca_pem=ca_pem)
