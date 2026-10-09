"""Tests for the eBus-to-YAML translation layer (clone.py)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest
import yaml
from ebus_sdk import DiscoveredDevice

from panelbench.clone import (
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_EVSE,
    TYPE_PV,
    translate_panel_tree,
    translate_scraped_panel,
    update_config_from_scrape,
    write_clone_config,
)
from panelbench.scraper import ScrapedPanel
from panelbench.validation import validate_yaml_config
from tests._helpers import (
    CAPTURED_MAIN_32,
    CAPTURED_MAIN_32_SERIAL,
    CURRENT_FIRMWARE,
    EARLIER_FIRMWARE,
    discovered_from_tree_snapshot,
)

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
    settable: dict[str, set[str]] | None = None,
) -> DiscoveredDevice:
    """Build a DiscoveredDevice from a {capability: {property: value}} map.

    `settable` names the properties whose declaration carries `$settable`, as
    `{capability: {property, ...}}`. It is part of the published shape, not decoration:
    Homie 5 defaults the attribute to false, so a panel commissions a per-circuit lock
    by omitting it — which is the only thing `never-backup` puts on the wire.
    """
    device = DiscoveredDevice(device_id)
    settable = settable or {}
    nodes = {
        cap: {
            "name": cap,
            "properties": {
                prop: (
                    {"name": prop, "datatype": "string", "settable": True}
                    if prop in settable.get(cap, set())
                    else {"name": prop, "datatype": "string"}
                )
                for prop in props
            },
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
    never_backup: bool = False,
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
    # What a real panel declares: `switch/relay` settable exactly when the relay is
    # controllable, `load-shed/priority` settable exactly when the circuit is not
    # commissioned never-backup. The two locks are independent by construction.
    declared_settable: dict[str, set[str]] = {}
    if controllable == "true":
        declared_settable["switch"] = {"relay"}
    if not never_backup:
        declared_settable["load-shed"] = {"priority"}
    return _device(device_id, TYPE_CIRCUIT, caps, parent=_SERIAL, settable=declared_settable)


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
        """A circuit whose relay is not controllable maps to non-controllable, the
        spelling the shipped configs write.

        v1.0 publishes `switch/relay-controllable` directly; the flat schema inferred
        this from `always-on`.
        """
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        # Solar inverter (positions 7,9) is not relay-controllable.
        t = templates["clone_7"]
        assert isinstance(t, dict)
        assert t["relay_behavior"] == "non-controllable"

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

    def test_an_unknown_priority_reads_as_one_the_emitter_publishes_so(self) -> None:
        """The emitter publishes every priority carried over from the REST era as
        UNKNOWN, so a circuit publishing UNKNOWN is cloned with one of those, which it
        publishes as UNKNOWN again; no config can name UNKNOWN itself."""
        devices = _base_devices()
        devices["aaa111"].update_property("load-shed", "priority", "UNKNOWN")

        config = translate_scraped_panel(_make_scraped(devices))

        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        assert templates["clone_1"]["priority"] == "NICE_TO_HAVE"
        validate_yaml_config(config)

    def test_a_priority_the_emitter_does_not_take_is_refused(self) -> None:
        config = translate_scraped_panel(_make_scraped())
        templates = config["circuit_templates"]
        assert isinstance(templates, dict)
        templates["clone_1"]["priority"] = "UNKNOWN"

        with pytest.raises(ValueError, match=r"clone_1.*priority 'UNKNOWN'"):
            validate_yaml_config(config)

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
    """Tests for the clone's record of where it came from."""

    def test_panel_source_written_when_host_provided(self) -> None:
        """panel_source block is written when host is passed to translate."""
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100")
        ps = config.get("panel_source")
        assert isinstance(ps, dict)
        assert ps["origin_serial"] == _SERIAL
        assert ps["host"] == "192.168.1.100"
        assert "last_synced" in ps

    def test_the_clone_carries_no_passphrase(self) -> None:
        """It is kept in the panel secrets store, never in a config users share."""
        ps = translate_scraped_panel(_make_scraped(), host="192.168.1.100")["panel_source"]
        assert isinstance(ps, dict)
        assert "passphrase" not in ps

    def test_no_panel_source_without_host(self) -> None:
        """panel_source is omitted when host is not provided."""
        config = translate_scraped_panel(_make_scraped())
        assert "panel_source" not in config

    def test_panel_source_validates(self) -> None:
        """Config with panel_source passes validation."""
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100")
        validate_yaml_config(config)

    def test_panel_source_roundtrip(self, tmp_path: Path) -> None:
        """panel_source survives YAML write/load roundtrip."""
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100")
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
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100")

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
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100")

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
        config = translate_scraped_panel(_make_scraped(), host="192.168.1.100")
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

    def test_a_refresh_leaves_the_clone_firmware_alone(self) -> None:
        """A clone keeps the release it was taken at when its source later upgrades."""
        before = _base_devices()
        before[_SERIAL].update_property("info", "firmware-version", EARLIER_FIRMWARE)
        config = translate_scraped_panel(_make_scraped(before), host="192.168.1.100")
        assert config["firmware_version"] == EARLIER_FIRMWARE

        after = _base_devices()
        after[_SERIAL].update_property("info", "firmware-version", CURRENT_FIRMWARE)
        update_config_from_scrape(config, _make_scraped(after))

        assert config["firmware_version"] == EARLIER_FIRMWARE


def _templates(config: dict[str, object]) -> dict[str, dict[str, object]]:
    templates = config["circuit_templates"]
    assert isinstance(templates, dict)
    return templates


def _panel_config(config: dict[str, object]) -> dict[str, object]:
    panel = config["panel_config"]
    assert isinstance(panel, dict)
    return panel


def _locked_circuit(
    device_id: str,
    name: str,
    spaces: str,
    *,
    feeds: tuple[str, str] | None,
) -> DiscoveredDevice:
    """A circuit SPAN adds for a commissioned system: relay locked, priority locked
    at NEVER. Whatever it is named, and whatever it feeds, is the test's to choose."""
    return _circuit(
        device_id,
        name,
        spaces,
        rating="40",
        priority="NEVER",
        active_power="0.0",
        controllable="false",
        never_backup=True,
        feeds=feeds,
    )


class TestCommissionedSystemByWhatItFeeds:
    """A commissioned circuit is recognised by its locks and what it feeds.

    SPAN names these circuits "Commissioned PV System" and "Commissioned Backup
    System", but the name is the user's to change. Reading it as the commissioning
    lost the lock from any clone of a renamed one.
    """

    def test_a_locked_circuit_feeding_an_inverter_is_the_pv_system_whatever_its_name(
        self,
    ) -> None:
        devices = _base_devices()
        devices["ccc333"] = _locked_circuit(
            "ccc333", "Roof Array", "7,9", feeds=("pv-0", "energy.ebus.device.pv")
        )

        config = translate_scraped_panel(_make_scraped(devices))

        assert _templates(config)["clone_7"].get("commissioned_system") == "pv"
        validate_yaml_config(config)

    def test_a_locked_circuit_feeding_a_battery_is_the_backup_system(self) -> None:
        devices = _base_devices()
        devices["ddd444"] = _locked_circuit(
            "ddd444", "Powerwall", "11,13", feeds=("bess-0", "energy.ebus.device.bess")
        )

        config = translate_scraped_panel(_make_scraped(devices))

        assert _templates(config)["clone_11"].get("commissioned_system") == "backup"
        validate_yaml_config(config)

    def test_what_it_feeds_outranks_its_name(self) -> None:
        """A locked circuit feeding a SPAN Drive is no PV system, whatever it is called."""
        devices = _base_devices()
        devices["eee555"] = _locked_circuit(
            "eee555",
            "Commissioned PV System",
            "15,17",
            feeds=("evse-0", "energy.ebus.device.evse"),
        )

        config = translate_scraped_panel(_make_scraped(devices))

        assert "commissioned_system" not in _templates(config)["clone_15"]

    def test_without_connection_data_the_name_decides(self) -> None:
        """The fallback, for a panel that publishes no `connection` on the circuit."""
        devices = _base_devices()
        devices["ccc333"] = _locked_circuit("ccc333", "Commissioned PV System", "7,9", feeds=None)
        devices["ddd444"] = _locked_circuit("ddd444", "Battery Storage", "11,13", feeds=None)

        templates = _templates(translate_scraped_panel(_make_scraped(devices)))

        assert templates["clone_7"].get("commissioned_system") == "pv"
        assert "commissioned_system" not in templates["clone_11"]

    def test_feeding_an_inverter_without_both_locks_is_not_commissioned(self) -> None:
        """The base fixture's solar circuit: relay locked, priority still settable."""
        templates = _templates(translate_scraped_panel(_make_scraped()))

        assert "commissioned_system" not in templates["clone_7"]

    def test_under_a_variant_whose_relay_lock_locks_the_priority_it_is_that_lock_alone(
        self,
    ) -> None:
        """That variant has no commissioned-system circuits: a battery's breaker there is
        an ordinary circuit with a locked relay at priority NEVER, and nothing more is
        recorded for it."""
        devices = _base_devices()
        devices[_SERIAL].update_property("info", "hardware-version", "3.0")
        devices["ddd444"] = _locked_circuit(
            "ddd444", "Powerwall", "11,13", feeds=("bess-0", "energy.ebus.device.bess")
        )

        template = _templates(translate_scraped_panel(_make_scraped(devices)))["clone_11"]

        assert "commissioned_system" not in template
        assert "never_backup" not in template
        assert (template["relay_behavior"], template["priority"]) == ("non-controllable", "NEVER")

    def test_a_commissioned_system_under_that_variant_is_refused(self) -> None:
        devices = _base_devices()
        devices["ddd444"] = _locked_circuit(
            "ddd444", "Powerwall", "11,13", feeds=("bess-0", "energy.ebus.device.bess")
        )
        config = translate_scraped_panel(_make_scraped(devices))
        config["hardware_version"] = "3.0"

        with pytest.raises(ValueError, match="does not have: remove commissioned_system"):
            validate_yaml_config(config)


class TestWhereTheBatteryHangs:
    """The battery's place, as the panel publishes it, is where its clone puts it."""

    def test_a_battery_a_circuit_feeds_is_in_the_panel_fed_by_that_circuit(self) -> None:
        devices = _base_devices()
        devices["ddd444"] = _locked_circuit(
            "ddd444", "Powerwall", "11,13", feeds=("bess-0", "energy.ebus.device.bess")
        )

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert (bess["relative_position"], bess["feed"]) == ("IN_PANEL", "circuit_11")

    def test_a_battery_nothing_names_is_in_the_panel_with_no_feed(self) -> None:
        devices = _base_devices()
        devices["ddd444"] = _circuit(
            "ddd444", "Battery Storage", "11,13", rating="40", priority="NEVER", active_power="0.0"
        )

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert bess["relative_position"] == "IN_PANEL"
        assert "feed" not in bess


class TestPanelSize:
    """The panel's size is the model it publishes, not its highest occupied space."""

    def test_the_model_names_the_size(self) -> None:
        """A MAIN 40 whose circuits stop at space 17 is still a 40-space panel."""
        devices = _base_devices()
        devices[_SERIAL].update_property("info", "model", "MAIN_40")

        config = translate_scraped_panel(_make_scraped(devices))

        assert _panel_config(config)["total_tabs"] == 40
        assert config["unmapped_tabs"] == [
            t for t in range(1, 41) if t not in {1, 3, 5, 7, 9, 11, 13, 15, 17}
        ]
        validate_yaml_config(config)

    def test_the_model_itself_is_kept(self) -> None:
        """Published verbatim, rather than re-derived from the size it implies."""
        devices = _base_devices()
        devices[_SERIAL].update_property("info", "model", "MAIN_40")

        assert _panel_config(translate_scraped_panel(_make_scraped(devices)))["model"] == "MAIN_40"

    def test_a_model_its_highest_circuit_just_fits_is_kept(self) -> None:
        """The boundary: a circuit on the model's last space fits it."""
        devices = _base_devices()
        devices[_SERIAL].update_property("info", "model", "MAIN_17")

        assert _panel_config(translate_scraped_panel(_make_scraped(devices)))["total_tabs"] == 17

    def test_a_model_without_a_size_falls_back_to_the_highest_space(self) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property("info", "model", "UNKNOWN")

        assert _panel_config(translate_scraped_panel(_make_scraped(devices)))["total_tabs"] == 24

    def test_a_model_smaller_than_the_circuits_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A size the circuits do not fit in would make a clone the panel refuses."""
        devices = _base_devices()
        devices[_SERIAL].update_property("info", "model", "MAIN_16")

        config = translate_scraped_panel(_make_scraped(devices))

        assert _panel_config(config)["total_tabs"] == 24
        assert "MAIN_16" in caplog.text
        validate_yaml_config(config)


class TestPanelSiteValues:
    """The panel's time zone and line voltages travel with the clone."""

    def test_the_time_zone_is_copied(self) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property("status", "time-zone", "America/Denver")

        config = translate_scraped_panel(_make_scraped(devices))

        assert _panel_config(config)["time_zone"] == "America/Denver"
        validate_yaml_config(config)

    def test_the_line_voltages_are_copied(self) -> None:
        """One per-leg voltage is all a simulated panel publishes, so the clone takes
        the legs' mean, and their sum as the service voltage."""
        devices = _base_devices()
        devices[_SERIAL].update_property("meter", "voltage-a", "121.8")
        devices[_SERIAL].update_property("meter", "voltage-b", "122.1")

        panel = _panel_config(translate_scraped_panel(_make_scraped(devices)))

        assert panel["line_voltage_v"] == 121.95
        assert panel["service_voltage_v"] == 243.9

    def test_one_leg_stands_for_both(self) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property("meter", "voltage-a", "121.8")

        panel = _panel_config(translate_scraped_panel(_make_scraped(devices)))

        assert panel["line_voltage_v"] == 121.8
        assert panel["service_voltage_v"] == 243.6

    def test_nothing_published_writes_nothing(self) -> None:
        """Absent, the config's defaults apply rather than a guess written into it."""
        devices = _base_devices()
        devices[_SERIAL].update_property("meter", "voltage-a", "0.0")

        panel = _panel_config(translate_scraped_panel(_make_scraped(devices)))

        assert "time_zone" not in panel
        assert "line_voltage_v" not in panel
        assert "service_voltage_v" not in panel
        assert "postal_code" not in panel


class TestDefaultedBreakerRatings:
    """A rating the panel does not publish is carried through as absent where the
    config can say so, and reported where it cannot yet."""

    def test_a_missing_main_breaker_rating_is_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        devices = _base_devices()
        devices[_SERIAL] = _device(
            _SERIAL,
            TYPE_PANEL,
            {"info": {"serial-number": _SERIAL, "data-model-version": "1.0"}},
            children=[device_id for device_id in devices if device_id != _SERIAL],
        )

        with caplog.at_level(logging.WARNING, logger="panelbench.clone"):
            config = translate_scraped_panel(_make_scraped(devices))

        assert _panel_config(config)["main_size"] == 200
        assert any(
            _SERIAL in r.getMessage() and "200" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )

    def test_a_missing_circuit_rating_stays_missing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A clone is faithful to what the panel publishes: a circuit that publishes no
        rating gets none in the clone, rather than an invented one with a warning."""
        devices = _base_devices()
        devices["aaa111"].properties["breaker"].pop("rating")

        with caplog.at_level(logging.WARNING, logger="panelbench.clone"):
            config = translate_scraped_panel(_make_scraped(devices))

        templates = _templates(config)
        assert templates["clone_1"]["breaker_rating"] is None
        assert templates["clone_3"]["breaker_rating"] == 20
        assert not [r for r in caplog.records if "aaa111" in r.getMessage()]
        validate_yaml_config(config)


class TestFeedLookupStaysInThePanel:
    """Finding the circuit that feeds a device reads only this panel's circuits."""

    def test_another_panels_circuit_naming_the_same_inverter_is_ignored(self) -> None:
        """A broker serving two panels hands back both trees. A circuit of the other
        panel, listed first, that names this panel's inverter must not take its
        nameplate, which then lands on no template at all."""
        foreign = _device(
            "000foreign",
            TYPE_CIRCUIT,
            {
                "info": {"name": "Other Panel Solar", "spaces": "1"},
                "connection": {
                    "feeds-device-id": "pv-0",
                    "feeds-device-type": "energy.ebus.device.pv",
                },
            },
            parent="other-panel",
        )
        foreign_description = dict(foreign.description or {})
        foreign_description["root"] = "other-panel"
        foreign.update_description(json.dumps(foreign_description))
        devices = {"000foreign": foreign, **_base_devices()}

        config = translate_scraped_panel(_make_scraped(devices))

        ep = _templates(config)["clone_7"]["energy_profile"]
        assert isinstance(ep, dict)
        assert ep["nameplate_capacity_w"] == 5000.0


class TestTheCapturedPanel:
    """The release's masked capture of a real MAIN 32 on SPAN release 202639."""

    def test_a_clone_of_it_reads_what_the_panel_publishes(self) -> None:
        devices = discovered_from_tree_snapshot(CAPTURED_MAIN_32)

        config = translate_panel_tree(CAPTURED_MAIN_32_SERIAL, devices)

        panel = _panel_config(config)
        assert panel["total_tabs"] == 32
        assert panel["time_zone"] == "America/Los_Angeles"
        assert panel["line_voltage_v"] == 121.95
        assert panel["service_voltage_v"] == 243.9
        commissioned = [
            t for t in _templates(config).values() if t.get("commissioned_system") == "pv"
        ]
        assert len(commissioned) == 1
        validate_yaml_config(config)


def _circuits_by_id(config: dict[str, object]) -> dict[str, dict[str, object]]:
    circuits = config["circuits"]
    assert isinstance(circuits, list)
    return {str(c["id"]): c for c in circuits}


class TestWhatTheClonePublishesComesFromThePanel:
    """Values a panel publishes reach its clone, rather than PanelBench's defaults."""

    def test_each_circuits_pcs_priority_is_copied(self) -> None:
        devices = _base_devices()
        devices["aaa111"].update_property("pcs", "priority", "7")

        circuits = _circuits_by_id(translate_scraped_panel(_make_scraped(devices)))

        assert circuits["circuit_1"]["pcs_priority"] == 7

    def test_a_circuit_without_a_pcs_priority_has_none(self) -> None:
        """The capture's commissioned PV circuit publishes none."""
        devices = _base_devices()
        devices["aaa111"].properties["pcs"].pop("priority")

        circuits = _circuits_by_id(translate_scraped_panel(_make_scraped(devices)))

        assert circuits["circuit_1"]["pcs_priority"] is None

    def test_the_shed_threshold_is_copied(self) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property(
            "shed",
            "policy",
            '{"algorithm": "soc-priority.v1", "parameters": '
            '{"soc-threshold-shed": 49, "soc-threshold-release": 51}}',
        )

        assert (
            _panel_config(translate_scraped_panel(_make_scraped(devices)))["soc_shed_threshold"]
            == 49
        )

    def test_the_panel_vendor_is_copied(self) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property("info", "vendor-name", "SPAN")

        assert _panel_config(translate_scraped_panel(_make_scraped(devices)))["vendor_name"] == (
            "SPAN"
        )


class TestBatteryPowerLimits:
    """A battery's charge and discharge limits follow the battery, not a constant."""

    def test_a_published_nominal_power_sets_both_limits(self) -> None:
        devices = _base_devices()
        devices["bess-0"].update_property("info", "nominal-power", "11500")

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert (bess["max_charge_w"], bess["max_discharge_w"]) == (11500.0, 11500.0)

    def test_without_one_the_limits_scale_with_the_capacity(self) -> None:
        """5 kW per 13.5 kWh, a Powerwall 2's continuous rating: six of them, as the
        real MAIN 32 capture has, are 81 kWh and 30 kW, where the clone gave 3.5 kW."""
        devices = _base_devices()
        devices["bess-0"].update_property("info", "nameplate-capacity", "81")

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert bess["max_charge_w"] == pytest.approx(30000.0)
        assert bess["max_discharge_w"] == pytest.approx(30000.0)


class TestMicrogridInterconnect:
    """The MID's own identity reaches the clone, not one derived from the battery's."""

    def test_the_mids_serial_and_firmware_are_copied(self) -> None:
        devices = _base_devices()
        devices["mid-0"] = _device(
            "mid-0",
            "energy.ebus.device.mid",
            {"info": {"serial-number": "example-mid-0001", "firmware-version": "1.2.3"}},
            parent="bess-0",
        )

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert bess["mid_serial_number"] == "example-mid-0001"
        assert bess["mid_firmware_version"] == "1.2.3"

    def test_the_mids_vendor_model_and_hardware_are_copied(self) -> None:
        devices = _base_devices()
        devices["mid-0"] = _device(
            "mid-0",
            "energy.ebus.device.mid",
            {
                "info": {
                    "vendor-name": "Example Gateway Co",
                    "model": "Example Gateway",
                    "hardware-version": "B",
                }
            },
            parent="bess-0",
        )

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert bess["mid_vendor"] == "Example Gateway Co"
        assert bess["mid_product_name"] == "Example Gateway"
        assert bess["mid_hardware_version"] == "B"

    def test_only_the_batterys_own_mid_is_read(self) -> None:
        """A MID listed first that belongs to no battery here must not lend its serial."""
        devices = _base_devices()
        devices["mid-a"] = _device(
            "mid-a",
            "energy.ebus.device.mid",
            {"info": {"serial-number": "example-stray-mid"}},
            parent="another-bess",
        )
        devices["mid-b"] = _device(
            "mid-b",
            "energy.ebus.device.mid",
            {"info": {"serial-number": "example-mid-0001"}},
            parent="bess-0",
        )

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert bess["mid_serial_number"] == "example-mid-0001"


class TestNetworkAndEnvelope:
    """The panel's network configuration travels with the clone, absences included;
    its live state does not.

    The captured MAIN 32 is on Ethernet: `status/wifi` false, `status/ethernet` true
    and no SSID. A clone that published Wi-Fi up on `sim-wifi` invented a network.
    """

    def test_an_ethernet_panel_clones_with_no_wifi_and_no_ssid(self) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property("status", "wifi", "false")
        devices[_SERIAL].update_property("status", "ethernet", "true")

        panel = _panel_config(translate_scraped_panel(_make_scraped(devices)))

        assert (panel["wifi_link"], panel["ethernet_link"]) == (False, True)
        assert "wifi_ssid" in panel and panel["wifi_ssid"] is None

    def test_a_wifi_panel_keeps_its_ssid(self) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property("status", "wifi", "true")
        devices[_SERIAL].update_property("status", "wifi-ssid", "example-net")

        panel = _panel_config(translate_scraped_panel(_make_scraped(devices)))

        assert (panel["wifi_link"], panel["wifi_ssid"]) == (True, "example-net")

    def test_the_door_and_cloud_state_are_live_state_and_not_copied(self) -> None:
        """Copied, they would be frozen at the moment of the clone: a door opened to
        register the clone, or a cloud outage then, reported by the clone for good."""
        devices = _base_devices()
        devices[_SERIAL].update_property("door", "state", "OPEN")
        devices[_SERIAL].update_property("status", "cloud-connection", "UNKNOWN")

        panel = _panel_config(translate_scraped_panel(_make_scraped(devices)))

        assert not {"door_state", "cloud_connection"} & panel.keys()

    def test_a_panel_that_publishes_none_of_it_gets_none_written(self) -> None:
        panel = _panel_config(translate_scraped_panel(_make_scraped()))

        assert (
            not {
                "wifi_link",
                "ethernet_link",
                "wifi_ssid",
                "door_state",
                "cloud_connection",
                "model",
            }
            & panel.keys()
        )


class TestUnreadableNumbers:
    """A non-finite number a panel publishes is read as unpublished, not as a value."""

    @pytest.mark.parametrize("raw", ["inf", "-inf", "nan"])
    def test_a_non_finite_pcs_priority_is_absent(self, raw: str) -> None:
        devices = _base_devices()
        devices["aaa111"].update_property("pcs", "priority", raw)

        assert (
            _circuits_by_id(translate_scraped_panel(_make_scraped(devices)))["circuit_1"][
                "pcs_priority"
            ]
            is None
        )

    @pytest.mark.parametrize("raw", ["inf", "nan", "0", "-5"])
    def test_an_unusable_battery_power_falls_back_to_the_scaled_one(self, raw: str) -> None:
        devices = _base_devices()
        devices["bess-0"].update_property("info", "nominal-power", raw)

        bess = translate_scraped_panel(_make_scraped(devices))["bess"]

        assert isinstance(bess, dict)
        assert bess["max_charge_w"] == pytest.approx(13.5 * 5000.0 / 13.5)

    def test_a_shed_policy_without_a_threshold_is_reported_and_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        devices = _base_devices()
        devices[_SERIAL].update_property("shed", "policy", '{"algorithm": "soc-priority.v1"}')

        with caplog.at_level(logging.WARNING, logger="panelbench.clone"):
            panel = _panel_config(translate_scraped_panel(_make_scraped(devices)))

        assert "soc_shed_threshold" not in panel
        assert "soc-threshold-shed" in caplog.text
