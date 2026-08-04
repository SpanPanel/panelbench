"""Setter registry, /set subscription computation, and dispatch."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from span_panel_simulator.ebus_emitter.exceptions import MissingSetterError

_LOG = logging.getLogger(__name__)

SetterHandler = Callable[[str, str, str, object], Awaitable[None]]


@dataclass(slots=True)
class SetSubscription:
    topic_pattern: str
    entity_class: str
    instance_id: str
    property_path: str
    datatype: str
    handler: SetterHandler


class SetterRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], SetterHandler] = {}

    def register(
        self,
        entity_class: str,
        property_path: str,
        handler: SetterHandler,
    ) -> None:
        self._handlers[(entity_class, property_path)] = handler

    def get(self, entity_class: str, property_path: str) -> SetterHandler | None:
        return self._handlers.get((entity_class, property_path))


def compute_subscriptions(
    *,
    instances: list[tuple[str, str]],
    settables_by_class: dict[str, list[tuple[str, str]]],
    registry: SetterRegistry,
    domain: str,
    bus_version: str,
    device_id_for: Callable[[str, str], str],
    node_id_for: Callable[[str, str, str], str] | None = None,
    datatype_for: Callable[[str, str, str], str] | None = None,
) -> list[SetSubscription]:
    if datatype_for is None:

        def datatype_for_default(_ec: str, _cap: str, _key: str) -> str:
            return "string"

        datatype_for = datatype_for_default
    if node_id_for is None:

        def node_id_for_default(_ec: str, iid: str, cap: str) -> str:
            del _ec, iid
            return cap

        node_id_for = node_id_for_default

    missing: list[tuple[str, str]] = []
    declared_classes: set[str] = set()
    for ec, _iid in instances:
        declared_classes.add(ec)
    for ec in declared_classes:
        for cap, key in settables_by_class.get(ec, []):
            prop_path = f"{cap}/{key}"
            if registry.get(ec, prop_path) is None:
                missing.append((ec, prop_path))

    if missing:
        raise MissingSetterError(missing=sorted(set(missing)))

    subs: list[SetSubscription] = []
    for ec, iid in instances:
        device_id = device_id_for(ec, iid)
        for cap, key in settables_by_class.get(ec, []):
            node_id = node_id_for(ec, iid, cap)
            prop_path = f"{cap}/{key}"
            handler = registry.get(ec, prop_path)
            assert handler is not None
            subs.append(
                SetSubscription(
                    topic_pattern=f"{domain}/{bus_version}/{device_id}/{node_id}/{key}/set",
                    entity_class=ec,
                    instance_id=iid,
                    property_path=prop_path,
                    datatype=datatype_for(ec, cap, key),
                    handler=handler,
                )
            )
    return subs


async def dispatch(
    topic: str,
    payload: bytes,
    subscriptions: list[SetSubscription],
) -> None:
    """Topic miss → log + drop. Decode failure → log + drop. Handler raises → log at
    ERROR with full context, then re-raise (fail-fast)."""
    for sub in subscriptions:
        if sub.topic_pattern != topic:
            continue
        try:
            value = _decode(payload, sub.datatype)
        except Exception:
            _LOG.warning(
                "set decode failed for topic=%s payload=%r datatype=%s",
                topic,
                payload,
                sub.datatype,
            )
            return
        try:
            await sub.handler(sub.entity_class, sub.instance_id, sub.property_path, value)
        except Exception:
            _LOG.exception(
                "setter handler raised: entity_class=%s instance_id=%s property_path=%s value=%r",
                sub.entity_class,
                sub.instance_id,
                sub.property_path,
                value,
            )
            raise
        return
    _LOG.warning("/set topic miss: %s", topic)


def _decode(payload: bytes, datatype: str) -> object:
    text = payload.decode("utf-8")
    match datatype:
        case "float":
            return float(text)
        case "integer":
            return int(text)
        case "boolean":
            return text.lower() in ("true", "1")
        case _:
            return text
