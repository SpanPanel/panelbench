"""Commissioning facts: what a panel records about itself and publishes as recorded.

A SPAN panel publishes some properties exactly as they were set when it was
commissioned, never as readings: a circuit's load tags and the rooms it serves, the
site's name and address, the lugs' service rating. The emitter takes each as a
manifest metadata key and publishes it verbatim where the panel's variant declares the
property, and nowhere else.

Each fact is listed once here, by the property it publishes, the config key holding
it and the metadata key the emitter reads, so the two directions use one table:
``spec_generator`` writes a config's facts into the manifest, and ``clone`` reads a
panel's back into a config. A fact the config leaves out is not published.

The same goes for what a panel declares and leaves unvalued, which every device
section of a config may list as ``unvalued``: the emitter's ``unvalued`` key, so the
reproduction leaves the same properties unvalued.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final


def _text(value: str) -> object:
    return value


def _flag(value: str) -> object:
    return value.lower() == "true"


def _whole(value: str) -> object:
    return int(float(value))


def _number(value: str) -> object:
    return float(value)


def _items(value: str) -> object:
    return [item for item in value.split(",") if item]


@dataclass(frozen=True)
class Fact:
    """One commissioning fact: the property, its config key and its metadata key."""

    path: str
    key: str
    metadata: str
    read: Callable[[str], object] = _text
    """The config value for the property's published value."""


def wire_value(value: object) -> str:
    """A config value as the metadata string the emitter publishes verbatim."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


SITE: Final = (
    Fact("info/name", "name", "site-name"),
    Fact("info/address-lines", "address_lines", "address-lines"),
    Fact("info/locality", "locality", "locality"),
    Fact("info/region", "region", "region"),
    Fact("info/country-code", "country_code", "country-code"),
    Fact(
        "info/utility-meter-serial-number",
        "utility_meter_serial_number",
        "utility-meter-serial-number",
    ),
)
"""The site, kept in ``panel_config.site``."""

SITE_LOCATION: Final = (
    Fact("info/latitude", "latitude", "latitude", _number),
    Fact("info/longitude", "longitude", "longitude", _number),
)
"""The site's location, kept in ``panel_config`` itself, which the simulation reads too."""

IMPORT_LIMITS: Final = (
    Fact(
        "pcs/off-grid-import-limit-enablement",
        "off_grid_import_limit_enablement",
        "off-grid-import-limit-enablement",
    ),
    Fact(
        "pcs/off-grid-import-limit", "off_grid_import_limit_a", "off-grid-import-limit-a", _number
    ),
    Fact(
        "pcs/operator-import-limit-enablement",
        "operator_import_limit_enablement",
        "operator-import-limit-enablement",
    ),
)
"""The commissioned import limits, kept in ``panel_config``."""

LUGS: Final = (
    Fact("connection/feeds-role", "feeds_role", "feeds-role"),
    Fact("connection/backed-up", "backed_up", "backed-up"),
    Fact("connection/service-rating", "service_rating_a", "service-rating-a", _whole),
    Fact(
        "connection/overcurrent-protection",
        "overcurrent_protection_a",
        "overcurrent-protection-a",
        _whole,
    ),
)
"""One set of lugs, kept in ``lugs.upstream`` or ``lugs.downstream``."""

CIRCUIT: Final = (
    Fact("info/tags", "tags", "tags", _items),
    Fact("info/locations", "locations", "locations", _items),
    Fact("info/dedicated", "dedicated", "dedicated", _flag),
    Fact("info/nominal-voltage", "nominal_voltage", "nominal-voltage", _number),
    Fact("breaker/protection-functions", "protection_functions", "protection-functions"),
    Fact("connection/feeds-role", "feeds_role", "feeds-role"),
    Fact("connection/backed-up", "backed_up", "backed-up"),
)
"""A circuit, kept on its circuit definition."""


def metadata(facts: tuple[Fact, ...], section: Mapping[str, object]) -> dict[str, str]:
    """The manifest metadata for each of *facts* that *section* states."""
    return {
        fact.metadata: wire_value(section[fact.key])
        for fact in facts
        if section.get(fact.key) is not None
    }


def config_values(
    facts: tuple[Fact, ...], published: Callable[[str], str | None]
) -> dict[str, object]:
    """The config entries for each of *facts* a device publishes a value for.

    *published* answers a property path with the device's value, or None.
    """
    found: dict[str, object] = {}
    for fact in facts:
        value = published(fact.path)
        if value is not None:
            found[fact.key] = fact.read(value)
    return found


def unvalued_metadata(listed: object, implied: Iterable[str] = ()) -> dict[str, str]:
    """The emitter's ``unvalued`` key, or nothing when nothing is unvalued.

    *listed* is a section's ``unvalued`` list; *implied* adds what the section says
    another way, such as a rating it records as absent.
    """
    paths = {str(path) for path in listed} if isinstance(listed, list) else set()
    paths.update(implied)
    return {"unvalued": ",".join(sorted(paths))} if paths else {}
