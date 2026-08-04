import pytest

from span_panel_simulator.flat_emitter.manifest import DeviceInstance, DeviceManifest


def _instance(
    entity_class: str = "circuit",
    instance_id: str = "c1",
    display_name: str = "Kitchen",
    **metadata: str,
) -> DeviceInstance:
    return DeviceInstance(
        entity_class=entity_class,
        instance_id=instance_id,
        display_name=display_name,
        metadata=metadata,
    )


def test_device_instance_is_frozen() -> None:
    inst = _instance()
    with pytest.raises(AttributeError):
        inst.entity_class = "panel"  # type: ignore[misc]


def test_manifest_get_returns_matching_instance() -> None:
    a = _instance(instance_id="c1")
    b = _instance(entity_class="panel", instance_id="p1", display_name="Span")
    manifest = DeviceManifest(instances=(a, b))
    assert manifest.get("circuit", "c1") is a
    assert manifest.get("panel", "p1") is b


def test_manifest_get_raises_on_unknown() -> None:
    manifest = DeviceManifest(instances=(_instance(),))
    with pytest.raises(KeyError):
        manifest.get("circuit", "missing")


def test_manifest_of_class_returns_all_matching() -> None:
    a = _instance(instance_id="c1")
    b = _instance(instance_id="c2")
    p = _instance(entity_class="panel", instance_id="p1")
    manifest = DeviceManifest(instances=(a, b, p))
    circuits = manifest.of_class("circuit")
    assert len(circuits) == 2
    assert {c.instance_id for c in circuits} == {"c1", "c2"}
    assert manifest.of_class("missing") == ()
