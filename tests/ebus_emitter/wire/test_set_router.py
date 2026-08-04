import pytest

from span_panel_simulator.ebus_emitter.exceptions import MissingSetterError
from span_panel_simulator.ebus_emitter.wire.set_router import (
    SetSubscription,
    SetterRegistry,
    compute_subscriptions,
    dispatch,
)


def _settables() -> dict[str, list[tuple[str, str]]]:
    return {
        "circuit": [("circuit", "relay"), ("circuit", "shed-priority")],
        "panel": [("core", "dominant-power-source")],
    }


def _instances() -> list[tuple[str, str]]:
    return [("circuit", "c1"), ("circuit", "c2"), ("panel", "p1")]


async def _noop(*_a: object, **_kw: object) -> None:
    return None


def test_compute_subscriptions_produces_one_per_settable_per_instance() -> None:
    reg = SetterRegistry()
    reg.register("circuit", "circuit/relay", _noop)
    reg.register("circuit", "circuit/shed-priority", _noop)
    reg.register("panel", "core/dominant-power-source", _noop)

    subs = compute_subscriptions(
        instances=_instances(),
        settables_by_class=_settables(),
        registry=reg,
        domain="ebus",
        bus_version="5",
        device_id_for=lambda ec, iid: "p1" if ec == "circuit" else iid,
        node_id_for=lambda _ec, iid, cap: iid if cap == "circuit" else cap,
    )
    assert len(subs) == 5
    topics = {s.topic_pattern for s in subs}
    assert "ebus/5/p1/c1/relay/set" in topics
    assert "ebus/5/p1/core/dominant-power-source/set" in topics


def test_compute_subscriptions_raises_on_missing_handler() -> None:
    reg = SetterRegistry()
    reg.register("circuit", "circuit/relay", _noop)

    with pytest.raises(MissingSetterError) as excinfo:
        compute_subscriptions(
            instances=_instances(),
            settables_by_class=_settables(),
            registry=reg,
            domain="ebus",
            bus_version="5",
            device_id_for=lambda ec, iid: "p1" if ec == "circuit" else iid,
            node_id_for=lambda _ec, iid, cap: iid if cap == "circuit" else cap,
        )
    assert ("circuit", "circuit/shed-priority") in excinfo.value.missing
    assert ("panel", "core/dominant-power-source") in excinfo.value.missing


@pytest.mark.asyncio
async def test_dispatch_invokes_handler_with_decoded_value() -> None:
    invoked: list[tuple[str, str, str, object]] = []

    async def handler(ec: str, iid: str, pp: str, value: object) -> None:
        invoked.append((ec, iid, pp, value))

    sub = SetSubscription(
        topic_pattern="ebus/5/p1/c1/relay/set",
        entity_class="circuit",
        instance_id="c1",
        property_path="circuit/relay",
        datatype="enum",
        handler=handler,
    )
    await dispatch("ebus/5/p1/c1/relay/set", b"CLOSED", [sub])
    assert invoked == [("circuit", "c1", "circuit/relay", "CLOSED")]


@pytest.mark.asyncio
async def test_dispatch_drops_topic_miss() -> None:
    invoked: list[tuple[object, ...]] = []

    async def handler(*a: object) -> None:
        invoked.append(a)

    sub = SetSubscription(
        "ebus/5/p1/c1/relay/set", "circuit", "c1", "circuit/relay", "string", handler
    )
    await dispatch("unrelated/topic", b"x", [sub])
    assert invoked == []


@pytest.mark.asyncio
async def test_dispatch_re_raises_handler_exception() -> None:
    async def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("handler bug")

    sub = SetSubscription(
        topic_pattern="ebus/5/p1/c1/relay/set",
        entity_class="circuit",
        instance_id="c1",
        property_path="circuit/relay",
        datatype="enum",
        handler=boom,
    )
    with pytest.raises(RuntimeError, match="handler bug"):
        await dispatch("ebus/5/p1/c1/relay/set", b"CLOSED", [sub])
