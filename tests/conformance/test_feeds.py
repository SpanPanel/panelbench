from __future__ import annotations

import json
from pathlib import Path

import pytest

from panelbench.conformance.feeds import CaptureError, from_capture, from_devices


class _FakeDevice:
    def __init__(self, device_id: str) -> None:
        self._id = device_id

    def id(self) -> str:
        return self._id

    def description(self) -> dict[str, object]:
        return {"name": self._id, "type": "t", "nodes": {}, "children": []}


def test_from_devices_keys_by_device_id() -> None:
    documents = from_devices([_FakeDevice("a"), _FakeDevice("b")])
    assert sorted(documents) == ["a", "b"]


def test_from_capture_reads_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({"d1": {"name": "d", "type": "t", "nodes": {}, "children": []}}))
    assert list(from_capture(path)) == ["d1"]


def test_from_capture_rejects_a_non_object(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(CaptureError, match="mapping of device id"):
        from_capture(path)
