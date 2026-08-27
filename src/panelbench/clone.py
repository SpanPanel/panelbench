"""eBus-to-YAML translation — converts a discovered panel into a simulator config.

Pure data transformation: takes a ``ScrapedPanel`` (a tree of discovered Homie
devices) and produces a complete YAML config dict matching the ``SimulationConfig``
TypedDict shape.

Design principles:
  - Each circuit gets its own template (``clone_{first position}``) for per-circuit
    fidelity.  Users can consolidate via the dashboard later.
  - Energy profile mode is inferred from the circuit's ``connection`` capability,
    which names the DER it feeds.
  - No unit conversion is needed — all eBus power values are in watts.
  - The clone serial is ``{original_serial}-clone``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import yaml
from ebus_sdk import DiscoveredDevice

from panelbench.validation import validate_yaml_config

# Homie node type strings used by the scraping path to identify entity classes from
# a discovered panel's MQTT topology. Inlined here (rather than imported from a
# central module) because the scraping path is the only consumer post-emitter cutover.
TYPE_PANEL = "energy.ebus.device.distribution-enclosure"
TYPE_CIRCUIT = "energy.ebus.device.circuit"
TYPE_BESS = "energy.ebus.device.bess"
TYPE_PV = "energy.ebus.device.pv"
TYPE_EVSE = "energy.ebus.device.evse"

if TYPE_CHECKING:
    from pathlib import Path

    from panelbench.scraper import ScrapedPanel

_LOGGER = logging.getLogger(__name__)


def make_clone_serial(original_serial: str) -> str:
    """Derive the clone serial from an original panel serial.

    Ensures the ``sim-`` prefix and appends ``-clone``.
    """
    base = original_serial
    if not base.lower().startswith("sim-"):
        base = f"sim-{base}"
    return f"{base}-clone"


_NIGHT_CHARGING_HOURS: dict[int, float] = {
    0: 1.0,
    1: 1.0,
    2: 1.0,
    3: 1.0,
    4: 1.0,
    5: 1.0,
    6: 0.0,
    7: 0.0,
    8: 0.0,
    9: 0.0,
    10: 0.0,
    11: 0.0,
    12: 0.0,
    13: 0.0,
    14: 0.0,
    15: 0.0,
    16: 0.0,
    17: 0.0,
    18: 0.0,
    19: 0.0,
    20: 0.0,
    21: 0.0,
    22: 0.0,
    23: 0.0,
}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def translate_scraped_panel(
    scraped: ScrapedPanel,
    *,
    host: str | None = None,
    passphrase: str | None = None,
) -> dict[str, object]:
    """Translate a scraped panel into a simulator config dict.

    Args:
        scraped: The scraped panel data.
        host: Source panel IP/hostname (stored in panel_source for refresh).
        passphrase: Source panel passphrase (stored in panel_source for refresh).

    Returns a dict matching the ``SimulationConfig`` TypedDict shape,
    ready for YAML serialisation and ``validate_yaml_config()``.
    """
    root = scraped.serial_number
    circuit_nodes = _devices_of_type(scraped.devices, root, TYPE_CIRCUIT)
    bess_nodes = _devices_of_type(scraped.devices, root, TYPE_BESS)
    pv_nodes = _devices_of_type(scraped.devices, root, TYPE_PV)
    evse_nodes = _devices_of_type(scraped.devices, root, TYPE_EVSE)

    # Build feed cross-reference: circuit_uuid → device_type
    feed_map = _build_feed_map(scraped.devices, circuit_nodes)

    # Extract panel-level values
    main_breaker = _int_prop(scraped.devices, scraped.serial_number, "breaker", "rating") or 200

    # Derive panel size from maximum space value across all circuits
    total_tabs = _derive_total_tabs(scraped.devices, circuit_nodes)

    clone_serial = make_clone_serial(scraped.serial_number)

    panel_config: dict[str, object] = {
        "serial_number": clone_serial,
        "total_tabs": total_tabs,
        "main_size": main_breaker,
        "latitude": 37.7,
        "longitude": -122.4,
    }

    # Build per-circuit templates and definitions
    templates: dict[str, dict[str, object]] = {}
    circuits: list[dict[str, object]] = []
    used_tabs: set[int] = set()

    for node_uuid in sorted(circuit_nodes):
        result = _translate_circuit(
            scraped.devices,
            node_uuid,
            feed_map,
        )
        if result is None:
            continue

        template_name, template, circuit_def, tabs = result
        templates[template_name] = template
        circuits.append(circuit_def)
        used_tabs.update(tabs)

    # Enrich PV circuit template
    for pv_id in pv_nodes:
        _enrich_pv_template(scraped.devices, pv_id, feed_map, templates)

    # Enrich EVSE circuit templates
    for evse_id in evse_nodes:
        _enrich_evse_template(scraped.devices, evse_id, feed_map, templates)

    # Unmapped tabs
    all_tabs = set(range(1, total_tabs + 1))
    unmapped = sorted(all_tabs - used_tabs)

    config: dict[str, object] = {
        "panel_config": panel_config,
        "circuit_templates": templates,
        "circuits": circuits,
        "unmapped_tabs": unmapped,
        "simulation_params": {
            "update_interval": 5,
            "time_acceleration": 1.0,
            "noise_factor": 0.02,
            "enable_realistic_behaviors": True,
        },
    }

    # Build top-level BESS config (only when a battery is actually connected)
    for bess_id in bess_nodes:
        bess_cfg = _build_bess_config(scraped.devices, bess_id)
        if bess_cfg is not None:
            config["bess"] = bess_cfg

    if host is not None:
        panel_source: dict[str, object] = {
            "origin_serial": scraped.serial_number,
            "host": host,
            "passphrase": passphrase,
            "last_synced": datetime.now(UTC).isoformat(),
        }
        # Snapshot the original BESS config so the modeling Before pass
        # can reconstruct the clone-time energy system accurately.
        if "bess" in config:
            import copy

            panel_source["original_bess"] = copy.deepcopy(config["bess"])
        config["panel_source"] = panel_source

    _LOGGER.info(
        "Translated panel %s: %d circuits, %d templates, bess=%s, pv=%s, evse=%s",
        clone_serial,
        len(circuits),
        len(templates),
        bool(bess_nodes),
        bool(pv_nodes),
        bool(evse_nodes),
    )

    return config


def update_config_from_scrape(
    config: dict[str, object],
    scraped: ScrapedPanel,
) -> bool:
    """Update an existing config dict with fresh values from a scrape.

    Patches energy seeds and ``panel_source.last_synced`` in-place.
    Used by the startup refresh path.

    Note: typical_power is intentionally *not* updated here.  The eBus
    ``active-power`` property is an instantaneous snapshot, not a
    representative average.  The HA integration derives a more meaningful
    typical_power from historical observation and pushes it via
    ``apply_usage_profiles``.

    Returns True if any values were changed.
    """
    templates = config.get("circuit_templates")
    if not isinstance(templates, dict):
        return False

    circuit_nodes = _devices_of_type(scraped.devices, scraped.serial_number, TYPE_CIRCUIT)

    changed = False

    for node_uuid in circuit_nodes:
        spaces = _spaces_prop(scraped.devices, node_uuid)
        if not spaces:
            continue

        template_name = f"clone_{spaces[0]}"
        template = templates.get(template_name)
        if not isinstance(template, dict):
            continue

        ep = template.get("energy_profile")
        if not isinstance(ep, dict):
            continue

        # Update energy seeds. Enclosure frame: `exported-energy` is the
        # enclosure exporting to the circuit (consumption), `imported-energy` is
        # the circuit backfeeding the enclosure (production). See the seeding
        # comment in `_translate_circuit`.
        exported = _float_prop(scraped.devices, node_uuid, "meter", "exported-energy")
        if (
            exported is not None
            and exported > 0
            and ep.get("initial_consumed_energy_wh") != exported
        ):
            ep["initial_consumed_energy_wh"] = exported
            changed = True

        imported = _float_prop(scraped.devices, node_uuid, "meter", "imported-energy")
        if (
            imported is not None
            and imported > 0
            and ep.get("initial_produced_energy_wh") != imported
        ):
            ep["initial_produced_energy_wh"] = imported
            changed = True

    # Update last_synced timestamp
    panel_source = config.get("panel_source")
    if isinstance(panel_source, dict):
        panel_source["last_synced"] = datetime.now(UTC).isoformat()
        changed = True

    return changed


def clone_config_path(config_dir: Path, original_serial: str) -> Path:
    """Return the default clone config path for a given serial."""
    return config_dir / f"{original_serial}-clone.yaml"


def write_clone_config(
    config: dict[str, object],
    config_dir: Path,
    original_serial: str,
    *,
    filename: str | None = None,
) -> Path:
    """Validate and write a cloned config to the config directory.

    Args:
        config: The config dict to write.
        config_dir: Directory to write into.
        original_serial: Original panel serial (used for default filename).
        filename: Optional custom filename override (must end with .yaml).

    Returns the path to the written file.

    Raises:
        ValueError: If the config fails validation.
    """
    validate_yaml_config(config)

    if filename is None:
        filename = f"{original_serial}-clone.yaml"
    output_path = config_dir / filename
    output_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    _LOGGER.info("Wrote clone config to %s", output_path)
    return output_path


def update_config_location(config_path: Path, latitude: float, longitude: float) -> str:
    """Update latitude, longitude, and timezone in a YAML config file.

    Reads the existing config, sets the new coordinates and derived
    IANA timezone, then writes the file back.

    Args:
        config_path: Path to the YAML config file.
        latitude: Degrees north.
        longitude: Degrees east.

    Returns:
        The resolved IANA timezone string.

    Raises:
        ValueError: If the file does not contain a valid config dict.
    """
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Invalid config format in {config_path}"
        raise ValueError(msg)

    panel_cfg = raw.get("panel_config", {})
    panel_cfg["latitude"] = latitude
    panel_cfg["longitude"] = longitude

    from timezonefinder import TimezoneFinder

    tz_result = TimezoneFinder().timezone_at(lat=latitude, lng=longitude)
    tz_name: str = str(tz_result) if tz_result is not None else "America/Los_Angeles"
    panel_cfg["time_zone"] = tz_name

    raw["panel_config"] = panel_cfg

    config_path.write_text(
        yaml.dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    _LOGGER.info(
        "Updated location in %s: %.4f, %.4f → %s",
        config_path.name,
        latitude,
        longitude,
        tz_name,
    )
    return tz_name


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


# --- property access ------------------------------------------------------------
#
# These read the discovered tree rather than a topic map. Under parent/child every
# entity is its own Homie device with its own namespace, so a property is addressed
# by (device id, capability, property) instead of by string-building
# `<panel>/<node>/<prop>`. The SDK already resolved the topics during discovery;
# rebuilding them here would be a second implementation of the same rule, which is
# exactly the drift the emitting side avoids by asking the graph for its topics.


def _get_prop(
    devices: Mapping[str, DiscoveredDevice],
    device_id: str,
    capability: str,
    prop: str,
) -> str | None:
    """A single property value from the discovered tree, or None if absent."""
    device = devices.get(device_id)
    if device is None:
        return None
    value = device.get_property(capability, prop)
    return None if value is None else str(value)


def _float_prop(
    devices: Mapping[str, DiscoveredDevice],
    device_id: str,
    capability: str,
    prop: str,
) -> float | None:
    """A float property, or None if absent or unparseable."""
    raw = _get_prop(devices, device_id, capability, prop)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_prop(
    devices: Mapping[str, DiscoveredDevice],
    device_id: str,
    capability: str,
    prop: str,
) -> int | None:
    """An integer property, tolerating a float-formatted payload."""
    raw = _get_prop(devices, device_id, capability, prop)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _bool_prop(
    devices: Mapping[str, DiscoveredDevice],
    device_id: str,
    capability: str,
    prop: str,
) -> bool | None:
    """A boolean property (Homie convention: the strings ``true`` / ``false``)."""
    raw = _get_prop(devices, device_id, capability, prop)
    if raw is None:
        return None
    return raw.lower() == "true"


def _is_settable(
    devices: Mapping[str, DiscoveredDevice],
    device_id: str,
    capability: str,
    prop: str,
) -> bool:
    """Whether the panel declared this property settable, from its ``$description``.

    A declaration question, not a value one — the other helpers here read published
    values, and settability is never published as one. Homie 5 defaults ``$settable``
    to false, so an absent attribute and an explicit ``false`` are the same claim, and
    firmware publishes the absence.

    A device with no description at all reads as settable, so a capture that lost the
    declarations does not silently commission every circuit's lock.
    """
    device = devices.get(device_id)
    if device is None or not device.description:
        return True
    declaration = device.get_node_properties(capability).get(prop)
    if not isinstance(declaration, dict):
        return True
    return declaration.get("settable") is True


def _spaces_prop(
    devices: Mapping[str, DiscoveredDevice],
    device_id: str,
) -> list[int]:
    """The breaker positions a circuit occupies, from ``info/spaces``.

    Replaces the flat schema's ``space`` (a single integer) plus ``dipole`` (a bool
    whose True meant "also occupies space + 2"). v1.0 publishes the positions
    directly as a comma-separated string, so the +2 split-phase convention is no
    longer inferred here — the panel states it.
    """
    raw = _get_prop(devices, device_id, "info", "spaces")
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            _LOGGER.warning("Circuit %s has unparseable spaces %r", device_id, raw)
            return []
    return out


def _devices_of_type(
    devices: Mapping[str, DiscoveredDevice],
    root: str,
    device_type: str,
) -> list[str]:
    """Device ids of a given Homie ``$type`` belonging to ``root``'s tree.

    Flat read the panel's own ``nodes`` map, which listed every entity. Under
    parent/child each device declares its own type and its own ``root``, so the
    filter is on membership rather than on containment — a broker serving two panels
    hands back both trees in the same namespace.
    """
    out: list[str] = []
    for device_id, device in devices.items():
        if device.root_id != root:
            continue
        description = device.description
        if isinstance(description, dict) and description.get("type") == device_type:
            out.append(device_id)
    return sorted(out)


def _circuit_feeding(
    devices: Mapping[str, DiscoveredDevice],
    target_device_id: str,
) -> str | None:
    """The circuit that feeds ``target_device_id``, or None.

    The reverse of the flat schema's lookup. A DER used to name its circuit through
    ``feed``; now the circuit names the DER through ``connection/feeds-device-id``,
    so finding a DER's circuit means searching the circuits rather than reading one
    property off the DER.
    """
    for device_id in devices:
        if _get_prop(devices, device_id, "connection", "feeds-device-id") == target_device_id:
            return device_id
    return None


def _build_feed_map(
    devices: Mapping[str, DiscoveredDevice],
    circuit_nodes: list[str],
) -> dict[str, str]:
    """Build a mapping from circuit UUID to device type based on feed properties.

    The relationship reversed direction in v1.0. Flat had each DER carry a ``feed``
    naming its circuit; the parent/child model gives the CIRCUIT a ``connection``
    capability naming what it feeds. That is the better direction — the circuit is
    the thing that always exists, so the edge no longer disappears when a DER is
    absent — but it means reading the circuits, not the DERs.

    BESS is deliberately not mapped: its config goes to the top-level ``bess``
    section rather than onto a circuit template.
    """
    kind_by_type = {TYPE_PV: "pv", TYPE_EVSE: "evse"}
    feed_map: dict[str, str] = {}

    for circuit_uuid in circuit_nodes:
        target = _get_prop(devices, circuit_uuid, "connection", "feeds-device-id")
        if not target:
            continue
        target_type = _get_prop(devices, circuit_uuid, "connection", "feeds-device-type")
        kind = kind_by_type.get(target_type or "")
        if kind is not None:
            feed_map[circuit_uuid] = kind

    return feed_map


def _derive_total_tabs(
    devices: Mapping[str, DiscoveredDevice],
    circuit_nodes: list[str],
) -> int:
    """Derive panel size from the highest breaker position any circuit occupies.

    v1.0 publishes the positions directly in ``info/spaces``, so a 240 V circuit
    already reports both. The flat schema published one position plus a ``dipole``
    flag and left the consumer to infer the companion as position + 2 — that
    inference is gone, along with the class of bug where a panel wired against the
    convention was silently mis-sized.
    """
    max_space = 0
    for node_id in circuit_nodes:
        for space in _spaces_prop(devices, node_id):
            max_space = max(max_space, space)

    # Round up to standard panel sizes
    for standard_size in (16, 24, 32, 40, 48):
        if max_space <= standard_size:
            return standard_size

    return max_space


def _translate_circuit(
    devices: Mapping[str, DiscoveredDevice],
    node_uuid: str,
    feed_map: dict[str, str],
) -> tuple[str, dict[str, object], dict[str, object], list[int]] | None:
    """Translate a single circuit node into a template and definition.

    Returns (template_name, template_dict, circuit_def, tabs) or None
    if the circuit cannot be translated (missing space).
    """
    tabs = _spaces_prop(devices, node_uuid)
    if not tabs:
        _LOGGER.warning("Circuit %s has no info/spaces property, skipping", node_uuid)
        return None
    space = tabs[0]

    name = _get_prop(devices, node_uuid, "info", "name") or f"Circuit {space}"
    breaker_rating = _int_prop(devices, node_uuid, "breaker", "rating") or 20
    active_power = _float_prop(devices, node_uuid, "meter", "active-power")
    priority = _get_prop(devices, node_uuid, "load-shed", "priority") or "NEVER"
    # v1.0 publishes controllability directly as `switch/relay-controllable`, where
    # flat inferred it from `always-on`. Not a rename: `pcs/managed` is the opposite
    # sense (PCS manages this circuit), so mapping always-on onto it inverts the
    # meaning and makes every circuit read as non-controllable.
    relay_controllable = _bool_prop(devices, node_uuid, "switch", "relay-controllable")
    if relay_controllable is None:
        relay_controllable = True

    # The second commissioning lock, and the mirror of the relay one above. `never-backup`
    # has no published value of its own: the eBus schema migration guide maps it onto
    # exactly one thing, `load-shed/priority`'s Homie `$settable`, "published with
    # `$settable = !never-backup`", and describes the result as "locked-priority circuits
    # (commissioned permanently `OFF_GRID`) appear as `priority = OFF_GRID, $settable =
    # false`". So the absence of the attribute IS the lock, and a clone that read only the
    # value would reproduce a circuit any consumer could re-prioritise.
    #
    # Deliberately not derived from `priority == "NEVER"`. `NEVER` is an ordinary settable
    # value meaning "never shed"; a production capture publishes two `NEVER` circuits with
    # `$settable = true`, which no value-derived flag can produce.
    never_backup = not _is_settable(devices, node_uuid, "load-shed", "priority")
    if never_backup and priority != "OFF_GRID":
        # The panel contradicted itself: a circuit commissioned never-backup *is*
        # permanently OFF_GRID, and the emitter rejects the pair at construction rather
        # than silently rewriting either half. Keep the published priority, drop the lock,
        # and say so — a clone that cannot start is a worse answer than a clone missing
        # one lock from a panel that was already inconsistent.
        _LOGGER.warning(
            "Circuit %s declares load-shed/priority read-only but publishes priority=%s, "
            "not OFF_GRID; cloning without never_backup",
            node_uuid,
            priority,
        )
        never_backup = False

    # Multi-position means split-phase, which is how 240 V presents on this panel.
    voltage = 240.0 if len(tabs) > 1 else 120.0

    # Energy profile mode from feed cross-reference
    device_role = feed_map.get(node_uuid)
    mode = _device_role_to_mode(device_role)

    # Relay behavior
    relay_behavior = "controllable" if relay_controllable else "non_controllable"

    # Power range and typical power
    max_power = breaker_rating * voltage
    typical = abs(active_power) if active_power is not None else max_power * 0.3
    # Clamp typical to max
    typical = min(typical, max_power)

    if mode == "producer":
        power_range = [-max_power, 0.0]
        typical_power = -typical if typical > 0 else -max_power * 0.6
    elif mode == "bidirectional":
        power_range = [-max_power, max_power]
        typical_power = typical
    else:
        power_range = [0.0, max_power]
        typical_power = typical

    # Seed energy accumulators from scraped values.
    #
    # The wire is enclosure-framed: a circuit's `exported-energy` is energy the
    # enclosure exported TO the circuit (normal load consumption), and
    # `imported-energy` is energy the enclosure imported FROM the circuit
    # (backfeed). The simulator's own accumulators are device-framed, so
    # consumption seeds from `exported-energy` and production from
    # `imported-energy`. Requires ebus-emitter >= 0.2.1, which publishes the
    # enclosure frame on both power and energy.
    imported_energy = _float_prop(devices, node_uuid, "meter", "imported-energy")
    exported_energy = _float_prop(devices, node_uuid, "meter", "exported-energy")

    # Build template
    energy_profile: dict[str, object] = {
        "mode": mode,
        "power_range": power_range,
        "typical_power": typical_power,
        "power_variation": 0.1,
    }

    if exported_energy is not None and exported_energy > 0:
        energy_profile["initial_consumed_energy_wh"] = exported_energy
    if imported_energy is not None and imported_energy > 0:
        energy_profile["initial_produced_energy_wh"] = imported_energy

    template: dict[str, object] = {
        "energy_profile": energy_profile,
        "relay_behavior": relay_behavior,
        "priority": priority,
        "breaker_rating": breaker_rating,
    }

    # Written only when set. It is a commissioning lock a producer opts into, and
    # `manifest_physics.never_backup` reads an absent key as unlocked, so a `false` here
    # would add a line to every cloned template to say nothing.
    if never_backup:
        template["never_backup"] = True

    if device_role == "evse":
        template["device_type"] = "evse"
    elif device_role == "pv":
        template["device_type"] = "pv"

    # Circuit definition
    circuit_id = f"circuit_{space}"
    template_name = f"clone_{space}"

    circuit_def: dict[str, object] = {
        "id": circuit_id,
        "name": name,
        "template": template_name,
        "tabs": tabs,
    }

    return template_name, template, circuit_def, tabs


def _device_role_to_mode(device_role: str | None) -> str:
    """Map a device role from the feed map to an energy profile mode."""
    if device_role == "pv":
        return "producer"
    if device_role == "evse":
        return "bidirectional"
    return "consumer"


def _build_bess_config(
    devices: Mapping[str, DiscoveredDevice],
    bess_node_id: str,
) -> dict[str, object] | None:
    """Build top-level bess config from scraped BESS node properties.

    Returns ``None`` when the BESS node is an empty slot (no battery
    connected) — indicated by a missing or zero nameplate capacity.
    """
    nameplate = _float_prop(devices, bess_node_id, "info", "nameplate-capacity")
    if not nameplate:
        return None

    return {
        "enabled": True,
        "charge_mode": "custom",
        "nameplate_capacity_kwh": nameplate,
        "backup_reserve_pct": 20.0,
        "charge_efficiency": 0.95,
        "discharge_efficiency": 0.95,
        "max_charge_w": 3500.0,
        "max_discharge_w": 3500.0,
        "charge_hours": [0, 1, 2, 3, 4, 5],
        "discharge_hours": [16, 17, 18, 19, 20, 21],
    }


def _enrich_pv_template(
    devices: Mapping[str, DiscoveredDevice],
    pv_node_id: str,
    feed_map: dict[str, str],
    templates: dict[str, dict[str, object]],
) -> None:
    """Enrich the PV circuit template with nameplate capacity and solar profile."""
    circuit_uuid = _circuit_feeding(devices, pv_node_id)
    template = _find_template_for_feed(circuit_uuid, feed_map, templates, devices)
    if template is None:
        return

    # PV publishes `info/nominal-power`; `nameplate-capacity` is the BESS property.
    nameplate = _float_prop(devices, pv_node_id, "info", "nominal-power")
    if nameplate is not None and nameplate > 0:
        ep = template.get("energy_profile")
        if isinstance(ep, dict):
            ep["nameplate_capacity_w"] = nameplate
            ep["power_range"] = [-nameplate, 0.0]
            ep["typical_power"] = -nameplate * 0.6


def _enrich_evse_template(
    devices: Mapping[str, DiscoveredDevice],
    evse_node_id: str,
    feed_map: dict[str, str],
    templates: dict[str, dict[str, object]],
) -> None:
    """Enrich the EVSE circuit template with time-of-day charging profile."""
    circuit_uuid = _circuit_feeding(devices, evse_node_id)
    template = _find_template_for_feed(circuit_uuid, feed_map, templates, devices)
    if template is None:
        return

    template["time_of_day_profile"] = {
        "enabled": True,
        "hour_factors": dict(_NIGHT_CHARGING_HOURS),
    }


def _find_template_for_feed(
    circuit_uuid: str | None,
    feed_map: dict[str, str],
    templates: dict[str, dict[str, object]],
    devices: Mapping[str, DiscoveredDevice],
) -> dict[str, object] | None:
    """Find the template associated with a circuit UUID via the feed map.

    The feed map maps circuit_uuid -> device_role. Templates are keyed
    ``clone_{first position}``, so this resolves the circuit's ``info/spaces`` and
    takes the first entry — the same key ``_translate_circuit`` built it under.
    """
    if circuit_uuid is None:
        return None

    spaces = _spaces_prop(devices, circuit_uuid)
    if not spaces:
        return None

    return templates.get(f"clone_{spaces[0]}")
