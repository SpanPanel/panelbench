"""Tests for the eBus-to-YAML translation layer (clone.py)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml
from ebus_sdk import DiscoveredDevice

from panelbench.clone import (
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_EVSE,
    TYPE_PV,
    translate_scraped_panel,
    update_config_from_scrape,
    write_clone_config,
)
from panelbench.scraper import ScrapedPanel
from panelbench.validation import validate_yaml_config

if TYPE_CHECKING:
    from pathlib import Path

# A realistic parent/child device tree. Under v1.0 every entity is its own Homie
# device in its own namespace — circuits, BESS, PV and EVSE are SIBLINGS of the panel
# on the wire, not nodes hanging off it — so the fixture builds real
# `DiscoveredDevice` objects rather than a topic map. Property layout is taken from
# the emitter's own profiles, so a change there shows up here as a failure rather
# than as an invented shape that quietly disagrees with what a panel publishes.
_SERIAL = "nj-2316-1234"

TYPE_PANEL = "energy.ebus.device.distribution-enclosure"


def _device(
    device_id: str,
    device_type: str,
    capabilities: dict[str, dict[str, str]],
    *,
    parent: str | None = None,
    children: list[str] | None = None,
) -> DiscoveredDevice:
    """Build a DiscoveredDevice from a {capability: {property: value}} map."""
    device = DiscoveredDevice(device_id)
    nodes = {
        cap: {
            "name": cap,
            "properties": {prop: {"name": prop, "datatype": "string"} for prop in props},
        }
        for cap, props in capabilities.items()
    }
    description: dict[str, object] = {
        "homie": "5.0",
        "name": device_id,
        "type": device_type,
        "nodes": nodes,
        "root": _SERIAL,
    }
    if parent is not None:
        description["parent"] = parent
    if children:
        description["children"] = children
    device.update_description(json.dumps(description))
    for cap, props in capabilities.items():
        for prop, value in props.items():
            device.update_property(cap, prop, value)
    return device


def _circuit(
    device_id: str,
    name: str,
    spaces: str,
    *,
    rating: str,
    priority: str,
    active_power: str,
    imported: str = "0.0",
    exported: str = "0.0",
    managed: str = "true",
    controllable: str = "true",
    feeds: tuple[str, str] | None = None,
) -> DiscoveredDevice:
    """A circuit device.

    `spaces` is the v1.0 replacement for the flat `space` + `dipole` pair: the panel
    states the positions it occupies as a comma list, so the +2 split-phase companion
    is no longer inferred by the consumer.
    """
    caps: dict[str, dict[str, str]] = {
        "info": {"name": name, "spaces": spaces},
        "breaker": {"rating": rating, "poles": str(len(spaces.split(",")))},
        "switch": {
            "relay": "CLOSED",
            "relay-requester": "UNKNOWN",
            "relay-controllable": controllable,
        },
        "load-shed": {"priority": priority},
        "meter": {
            "active-power": active_power,
            "imported-energy": imported,
            "exported-energy": exported,
        },
        "pcs": {"managed": managed, "priority": "0"},
    }
    if feeds is not None:
        device_ref, device_kind = feeds
        caps["connection"] = {
            "feeds-device-id": device_ref,
            "feeds-device-type": device_kind,
            "feeds-device-status": "OK",
            "count": "1",
        }
    return _device(device_id, TYPE_CIRCUIT, caps, parent=_SERIAL)


def _base_devices() -> dict[str, DiscoveredDevice]:
    """The panel and its children, mirroring the pre-parent/child fixture's content."""
    circuits = {
        # Living Room Lights — single-pole, position 1, a load.
        # Enclosure frame: a load accumulates exported-energy (enclosure -> circuit).
        "aaa111": _circuit(
            "aaa111",
            "Living Room Lights",
            "1",
            rating="15",
            priority="NEVER",
            active_power="-150.0",
            exported="54321.0",
        ),
        # Kitchen Outlets — 240 V across positions 3 and 5.
        "bbb222": _circuit(
            "bbb222",
            "Kitchen Outlets",
            "3,5",
            rating="20",
            priority="SOC_THRESHOLD",
            active_power="-800.0",
        ),
        # Solar Inverter — backfeeding, so positive on the wire and accumulating
        # imported-energy in the enclosure frame.
        "ccc333": _circuit(
            "ccc333",
            "Solar Inverter",
            "7,9",
            rating="30",
            priority="NEVER",
            active_power="3000.0",
            imported="1234567.0",
            controllable="false",
            feeds=("pv-0", "energy.ebus.device.pv"),
        ),
        "ddd444": _circuit(
            "ddd444",
            "Battery Storage",
            "11,13",
            rating="40",
            priority="NEVER",
            active_power="-2000.0",
            controllable="false",
            feeds=("bess-0", "energy.ebus.device.bess"),
        ),
        "eee555": _circuit(
            "eee555",
            "SPAN Drive",
            "15,17",
            rating="50",
            priority="OFF_GRID",
            active_power="-7200.0",
            feeds=("evse-0", "energy.ebus.device.evse"),
        ),
    }

    devices: dict[str, DiscoveredDevice] = {
        _SERIAL: _device(
            _SERIAL,
            TYPE_PANEL,
            {
                "info": {"serial-number": _SERIAL, "data-model-version": "1.0"},
                "breaker": {"rating": "200"},
            },
            children=[*circuits, "bess-0", "pv-0", "evse-0"],
        ),
        "bess-0": _device(
            "bess-0",
            TYPE_BESS,
            {"info": {"nameplate-capacity": "13.5"}, "soc": {"soc": "85.0"}},
            parent=_SERIAL,
        ),
        "pv-0": _device(
            "pv-0",
            TYPE_PV,
            {"info": {"nominal-power": "5000.0"}},
            parent=_SERIAL,
        ),
        "evse-0": _device("evse-0", TYPE_EVSE, {"info": {"model": "SPAN Drive"}}, parent=_SERIAL),
    }
    devices.update(circuits)
    return devices


def _make_scraped(devices: dict[str, DiscoveredDevice] | None = None) -> ScrapedPanel:
    """Build a ScrapedPanel fixture."""
    return ScrapedPanel(
        serial_number=_SERIAL,
        devices=devices if devices is not None else _base_devices(),
        mqtts_port=8883,
        ca_pem=b"fake-ca-pem",
    )


class TestTranslateScrapedPanel:
    """Tests for translate_scraped_panel()."""

    def test_basic_structure(self) -> None:
        """Config has all required top-level sections."""
        config = translate_scraped_panel(_make_scraped())
        assert "panel_config" in config
        assert "circuit_templates" in config
        assert "circuits" in config
        assert "unmapped_tabs" in config
        assert "simulation_params" in config

    def test_serial_suffix(self) -> None:
        """Clone serial gets sim- prefix."""
        config = translate_scraped_panel(_make_scraped())
        panel = config["panel_config"]
        assert isinstance(panel, dict)
        assert panel["serial_number"] == f"sim-{_SERIAL}-clone"

    def test_main_breaker(self) -> None:
        """Main breaker rating is extracted from core properties."""
        config = translate_scraped_panel(_make_scraped())
        panel = config["panel_config"]
        assert isinstance(panel, dict)
        assert panel["main_size"] == 200

    def test_circuit_count(self) -> None:
        """All 5 circuit nodes produce circuit definitions."""
        config = translate_scraped_panel(_make_scraped())
        circuits = config["circuits"]
        assert isinstance(circuits, list)
        assert len(circuits) == 5

    def test_single_pole_tabs(self) -> None:
        """Single-pole circuit (space 1) has one tab."""
        config = translate_scraped_panel(_make_scraped())
        circuits = config["circuits"]
        assert isinstance(circuits, list)
        # Find circuit_1
        c1 = next(c for c in circuits if isinstance(c, dict) and c["id"] == "circuit_1")
        assert c1["tabs"] == [1]

    def test_double_pole_tabs(self) -> None:
        """240V circuit (space 3, dipole) has two tabs [3, 5]."""
        config = translate_scraped_panel(_make_scraped())
        circuits = config["circuits"]
        assert isinstance(circuits, list)
        c3 = next(c for c in circuits if isinstance(c, dict) and c["id"] == "circuit_3")
        assert c3["tabs"] == [3, 5]

    def test_pv_mode(self) -> None:
        """Circuit fed by PV node gets producer mode."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        # Space 7 is the PV circuit
        t = templates["clone_7"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["mode"] == "producer"
        assert t.get("device_type") == "pv"

    def test_bess_mode(self) -> None:
        """Cloned panel with BESS node gets top-level bess config."""
        config = translate_scraped_panel(_make_scraped())
        bess = config.get("bess")
        assert isinstance(bess, dict)
        assert bess["enabled"] is True
        assert bess["nameplate_capacity_kwh"] == 13.5

    def test_evse_mode(self) -> None:
        """Circuit fed by EVSE node gets bidirectional mode and evse device type."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_15"]
        assert isinstance(t, dict)
        assert t.get("device_type") == "evse"
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["mode"] == "bidirectional"
        assert "time_of_day_profile" in t

    def test_consumer_mode(self) -> None:
        """Regular circuit gets consumer mode."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_1"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["mode"] == "consumer"

    def test_non_controllable_relay(self) -> None:
        """A circuit whose relay is not controllable maps to non_controllable.

        v1.0 publishes `switch/relay-controllable` directly; the flat schema inferred
        this from `always-on`.
        """
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        # Solar inverter (positions 7,9) is not relay-controllable.
        t = templates["clone_7"]
        assert isinstance(t, dict)
        assert t["relay_behavior"] == "non_controllable"

    def test_controllable_relay(self) -> None:
        """Circuit with always-on=false gets controllable relay behavior."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_1"]
        assert isinstance(t, dict)
        assert t["relay_behavior"] == "controllable"

    def test_priority_passthrough(self) -> None:
        """Shed priority passes through from eBus to template."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t3 = templates["clone_3"]
        assert isinstance(t3, dict)
        assert t3["priority"] == "SOC_THRESHOLD"
        t15 = templates["clone_15"]
        assert isinstance(t15, dict)
        assert t15["priority"] == "OFF_GRID"

    def test_panel_size_derivation(self) -> None:
        """Panel size rounds up to standard size from max space+companion."""
        config = translate_scraped_panel(_make_scraped())
        panel = config["panel_config"]
        assert isinstance(panel, dict)
        # Max space is 15 (dipole), companion is 17 → round up to 24
        assert panel["total_tabs"] == 24

    def test_config_validates(self) -> None:
        """Produced config passes validate_yaml_config() without error."""
        config = translate_scraped_panel(_make_scraped())
        validate_yaml_config(config)

    def test_pv_nameplate_enrichment(self) -> None:
        """PV template gets nameplate_capacity_w and adjusted power range."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_7"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["nameplate_capacity_w"] == 5000.0
        assert ep["power_range"] == [-5000.0, 0.0]
        assert ep["typical_power"] == -3000.0


class TestWriteCloneConfig:
    """Tests for write_clone_config()."""

    def test_writes_yaml_file(self, tmp_path: Path) -> None:
        """Config is written as valid YAML to the config directory."""
        config = translate_scraped_panel(_make_scraped())
        output = write_clone_config(config, tmp_path, _SERIAL)
        assert output.exists()
        assert output.name == f"{_SERIAL}-clone.yaml"

        loaded = yaml.safe_load(output.read_text())
        assert loaded["panel_config"]["serial_number"] == f"sim-{_SERIAL}-clone"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        """Re-clone overwrites existing file."""
        config = translate_scraped_panel(_make_scraped())
        write_clone_config(config, tmp_path, _SERIAL)
        # Write again — should not raise
        output = write_clone_config(config, tmp_path, _SERIAL)
        assert output.exists()

    def test_roundtrip_validates(self, tmp_path: Path) -> None:
        """Written config can be loaded back and passes validation."""
        config = translate_scraped_panel(_make_scraped())
        output = write_clone_config(config, tmp_path, _SERIAL)
        loaded = yaml.safe_load(output.read_text())
        validate_yaml_config(loaded)


class TestEnergySeeding:
    """Tests for initial energy accumulator seeding from scraped data."""

    def test_consumer_exported_energy_seeded(self) -> None:
        """Consumer circuit gets initial_consumed_energy_wh from exported-energy.

        Enclosure frame: energy the enclosure exported to the circuit is that
        circuit's consumption."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_1"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["initial_consumed_energy_wh"] == 54321.0

    def test_zero_energy_not_seeded(self) -> None:
        """Zero-valued energy is not written (avoids overriding annual estimate)."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_1"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert "initial_produced_energy_wh" not in ep

    def test_producer_imported_energy_seeded(self) -> None:
        """Producer circuit gets initial_produced_energy_wh from imported-energy.

        Enclosure frame: energy the enclosure imported from the circuit is that
        circuit's production (backfeed)."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_7"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["initial_produced_energy_wh"] == 1234567.0

    def test_missing_energy_no_seed(self) -> None:
        """Circuits without energy topics get no initial energy seeds."""
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        # Kitchen Outlets (space 3) has no energy topics
        t = templates["clone_3"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert "initial_consumed_energy_wh" not in ep
        assert "initial_produced_energy_wh" not in ep


class TestPanelSource:
    """Tests for panel_source credential persistence."""

    def test_panel_source_written_when_host_provided(self) -> None:
        """panel_source block is written when host is passed to translate."""
        config = translate_scraped_panel(
            _make_scraped(), host="192.168.1.100", passphrase="secret"
        )
        ps = config.get("panel_source")
        assert isinstance(ps, dict)
        assert ps["origin_serial"] == _SERIAL
        assert ps["host"] == "192.168.1.100"
        assert ps["passphrase"] == "secret"
        assert "last_synced" in ps

    def test_no_panel_source_without_host(self) -> None:
        """panel_source is omitted when host is not provided."""
        config = translate_scraped_panel(_make_scraped())
        assert "panel_source" not in config

    def test_panel_source_null_passphrase(self) -> None:
        """panel_source supports null passphrase (door-bypass)."""
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100", passphrase=None)
        ps = config.get("panel_source")
        assert isinstance(ps, dict)
        assert ps["passphrase"] is None

    def test_panel_source_validates(self) -> None:
        """Config with panel_source passes validation."""
        config = translate_scraped_panel(
            _make_scraped(), host="192.168.1.100", passphrase="secret"
        )
        validate_yaml_config(config)

    def test_panel_source_roundtrip(self, tmp_path: Path) -> None:
        """panel_source survives YAML write/load roundtrip."""
        config = translate_scraped_panel(
            _make_scraped(), host="192.168.1.100", passphrase="secret"
        )
        output = write_clone_config(config, tmp_path, _SERIAL)
        loaded = yaml.safe_load(output.read_text())
        validate_yaml_config(loaded)
        ps = loaded["panel_source"]
        assert ps["origin_serial"] == _SERIAL
        assert ps["host"] == "192.168.1.100"


class TestUpdateConfigFromScrape:
    """Tests for the lightweight startup refresh (update_config_from_scrape)."""

    def test_typical_power_not_overwritten(self) -> None:
        """Active power snapshot must not overwrite typical_power."""
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100", passphrase=None)

        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_1"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        original_typical = ep["typical_power"]

        # Modify scraped data to simulate changed active-power
        devices = _base_devices()
        devices["aaa111"].update_property("meter", "active-power", "-250.0")
        updated_scraped = _make_scraped(devices)

        update_config_from_scrape(config, updated_scraped)

        # typical_power should be unchanged — eBus active-power is an
        # instantaneous snapshot, not a representative average.
        assert ep["typical_power"] == original_typical

    def test_energy_seeds_updated(self) -> None:
        """Energy accumulators are updated from new scrape."""
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100", passphrase=None)

        devices = _base_devices()
        # aaa111 is a load, so its consumption accumulator is exported-energy.
        devices["aaa111"].update_property("meter", "exported-energy", "99999.0")
        updated_scraped = _make_scraped(devices)

        changed = update_config_from_scrape(config, updated_scraped)
        assert changed is True

        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        t = templates["clone_1"]
        assert isinstance(t, dict)
        ep = t["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["initial_consumed_energy_wh"] == 99999.0

    def test_last_synced_updated(self) -> None:
        """panel_source.last_synced is updated on refresh."""
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100", passphrase=None)
        ps = config.get("panel_source")
        assert isinstance(ps, dict)
        old_synced = ps["last_synced"]

        import time

        time.sleep(0.01)  # ensure timestamp difference
        update_config_from_scrape(config, _make_scraped())

        assert ps["last_synced"] != old_synced

    def test_no_change_returns_false(self) -> None:
        """Returns False when scrape data matches existing config."""
        config = translate_scraped_panel(_make_scraped())
        # No panel_source → last_synced never updated → only data comparison
        # Remove panel_source to test pure data path
        changed = update_config_from_scrape(config, _make_scraped())
        assert changed is False
