"""Pure topic-template functions for Homie wire paths the SDK does not own."""

from __future__ import annotations


def root_state_topic(domain: str, bus_version: str, root_device_id: str) -> str:
    return f"{domain}/{bus_version}/{root_device_id}/$state"


def device_state_topic(domain: str, bus_version: str, device_id: str) -> str:
    return f"{domain}/{bus_version}/{device_id}/$state"


def device_description_topic(domain: str, bus_version: str, device_id: str) -> str:
    return f"{domain}/{bus_version}/{device_id}/$description"


def set_topic_for(
    domain: str,
    bus_version: str,
    device_id: str,
    capability: str,
    property_key: str,
) -> str:
    return f"{domain}/{bus_version}/{device_id}/{capability}/{property_key}/set"


def parse_set_topic(
    topic: str,
    domain: str,
    bus_version: str,
) -> tuple[str, str, str] | None:
    prefix = f"{domain}/{bus_version}/"
    if not topic.startswith(prefix) or not topic.endswith("/set"):
        return None
    rest = topic[len(prefix) : -len("/set")]
    parts = rest.split("/")
    if len(parts) != 3:
        return None
    device_id, capability, property_key = parts
    if capability.startswith("$") or property_key.startswith("$"):
        return None
    return device_id, capability, property_key
