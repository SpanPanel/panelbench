"""Tests for the eBus scraper (scraper.py)."""

from __future__ import annotations

import json

import pytest
from ebus_sdk import DiscoveredDevice

from panelbench.clone import TYPE_CIRCUIT, TYPE_PANEL
from panelbench.scraper import (
    PanelCredentials,
    ScrapeError,
    _validate_discovered_tree,
)

_SERIAL = "nj-2316-TEST"


class TestScrapeError:
    """Tests for the ScrapeError exception."""

    def test_phase_stored(self) -> None:
        err = ScrapeError("connecting", "timeout")
        assert err.phase == "connecting"
        assert str(err) == "timeout"


class TestPanelCredentials:
    """Tests for the PanelCredentials dataclass."""

    def test_frozen(self) -> None:
        creds = PanelCredentials(
            username="user",
            password="pass",
            serial_number=_SERIAL,
            mqtts_port=8883,
            broker_host="192.168.1.1",
        )
        assert creds.serial_number == _SERIAL
        with pytest.raises(AttributeError):
            creds.username = "other"  # frozen dataclass


class TestValidateDiscoveredTree:
    """Tests for `_validate_discovered_tree()`.

    Validation moved from topic strings to the tree, because the failure worth
    catching changed shape. A flat scrape could only really fail by returning
    nothing; a parent/child discovery can return the panel and none of its children,
    which looks like success to anything counting topics.
    """

    def _device(self, device_id: str, device_type: str, *, root: str) -> DiscoveredDevice:
        device = DiscoveredDevice(device_id)
        device.update_description(
            json.dumps(
                {
                    "homie": "5.0",
                    "name": device_id,
                    "type": device_type,
                    "root": root,
                    "nodes": {},
                }
            )
        )
        return device

    def _valid_tree(self) -> dict[str, DiscoveredDevice]:
        return {
            _SERIAL: self._device(_SERIAL, TYPE_PANEL, root=_SERIAL),
            "aaa111": self._device("aaa111", TYPE_CIRCUIT, root=_SERIAL),
        }

    def test_valid_passes(self) -> None:
        _validate_discovered_tree(self._valid_tree(), _SERIAL)

    def test_missing_root_raises(self) -> None:
        tree = self._valid_tree()
        del tree[_SERIAL]
        with pytest.raises(ScrapeError, match="No device published"):
            _validate_discovered_tree(tree, _SERIAL)

    def test_panel_without_circuits_raises(self) -> None:
        """The parent/child-specific failure: the root arrived, its children did not."""
        tree = {_SERIAL: self._device(_SERIAL, TYPE_PANEL, root=_SERIAL)}
        with pytest.raises(ScrapeError, match="none of its circuits"):
            _validate_discovered_tree(tree, _SERIAL)

    def test_circuits_from_another_tree_do_not_count(self) -> None:
        """A circuit rooted elsewhere is a different panel's device.

        Under parent/child every device shares the `ebus/5/` namespace, so a broker
        serving two panels hands back both. Membership is `root`, not topic prefix.
        """
        tree = self._valid_tree()
        tree["zzz999"] = self._device("zzz999", TYPE_CIRCUIT, root="some-other-panel")
        del tree["aaa111"]
        with pytest.raises(ScrapeError, match="none of its circuits"):
            _validate_discovered_tree(tree, _SERIAL)
