"""A panel definition, imported as a PanelBench config."""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from ebus_panel_sim import (
    DeviceManifest,
    LoadSheddingConfig,
    ManifestPhysicsView,
    PanelDefinition,
    dump_definition,
)
from ebus_panel_sim.capture import definition_from_tree, tree_from_retained

from panelbench.dashboard import DashboardContext, create_dashboard_app
from panelbench.dashboard.keys import APP_KEY_STORE
from panelbench.definition_import import config_from_definition
from panelbench.emitter_adapter.definition import (
    bess_config,
    bess_dispatch_yaml,
    build_definition,
    load_shedding_config,
    load_shedding_yaml,
)
from panelbench.emitter_adapter.wire_capture import capture_retained
from panelbench.validation import validate_yaml_config
from tests._helpers import DEFAULT_CONFIG, EARLIER_FIRMWARE, default_config, write_config


def _definition() -> PanelDefinition:
    return build_definition(default_config())


async def _runs(config: dict[str, object], tmp_path: Path) -> None:
    """The imported config loads and publishes like any other."""
    assert await capture_retained(write_config(tmp_path / "imported.yaml", config))


@pytest.mark.asyncio
async def test_an_imported_definition_is_a_config_that_runs(tmp_path: Path) -> None:
    imported = config_from_definition(_definition())

    validate_yaml_config(imported)
    await _runs(imported, tmp_path)


def test_battery_dispatch_comes_from_the_definition() -> None:
    definition = _definition()
    battery = dataclasses.replace(definition.bess_configs[0], max_charge_w=1234.0)

    imported = config_from_definition(dataclasses.replace(definition, bess_configs=(battery,)))

    bess = imported["bess"]
    assert isinstance(bess, dict)
    assert bess["max_charge_w"] == 1234.0


def test_the_starting_charge_is_the_manifests() -> None:
    """The emitter seeds the battery from the manifest's `initial-soe-kwh`, not from
    `BESSConfig.initial_soc_pct`, so the import must keep the manifest's value."""
    definition = _definition()
    seeded = ManifestPhysicsView(definition.manifest).bess(definition.bess_configs[0].instance_id)
    assert seeded.initial_soe_kwh is not None
    battery = dataclasses.replace(definition.bess_configs[0], initial_soc_pct=90.0)

    imported = config_from_definition(dataclasses.replace(definition, bess_configs=(battery,)))

    bess = imported["bess"]
    assert isinstance(bess, dict)
    assert bess["initial_soe_kwh"] == seeded.initial_soe_kwh
    assert bess["initial_soe_kwh"] != round(battery.nameplate_capacity_kwh * 0.9, 3)


def test_without_a_manifest_charge_the_percentage_is_used() -> None:
    definition = _definition()
    unseeded = DeviceManifest(
        instances=tuple(
            dataclasses.replace(
                i, metadata={k: v for k, v in i.metadata.items() if k != "initial-soe-kwh"}
            )
            if i.entity_class == "bess"
            else i
            for i in definition.manifest.instances
        )
    )
    battery = dataclasses.replace(definition.bess_configs[0], initial_soc_pct=40.0)

    yaml_bess = bess_dispatch_yaml(battery, unseeded)

    assert yaml_bess["initial_soe_kwh"] == round(battery.nameplate_capacity_kwh * 0.4, 3)


def test_the_shed_threshold_comes_from_the_definition() -> None:
    definition = dataclasses.replace(
        _definition(), load_shedding=LoadSheddingConfig(soc_threshold_pct=37.0)
    )

    panel = config_from_definition(definition)["panel_config"]

    assert isinstance(panel, dict)
    assert panel["soc_shed_threshold"] == 37.0


def test_two_batteries_are_refused() -> None:
    definition = _definition()
    first = definition.bess_configs[0]
    second = dataclasses.replace(first, instance_id="second-bess")

    with pytest.raises(ValueError, match="one battery per panel"):
        config_from_definition(dataclasses.replace(definition, bess_configs=(first, second)))


def test_a_battery_config_naming_no_battery_device_is_refused() -> None:
    """Upstream builds the battery from the config alone, so a config whose id names
    no `bess` device renders; its dispatch settings would then belong to nothing."""
    definition = _definition()
    stray = dataclasses.replace(definition.bess_configs[0], instance_id="not-a-device")

    with pytest.raises(ValueError, match="'not-a-device'"):
        config_from_definition(dataclasses.replace(definition, bess_configs=(stray,)))


def test_dispatch_yaml_inverts_bess_config() -> None:
    battery = _definition().bess_configs[0]

    rebuilt = bess_config(
        "sim-40t-001", {"enabled": True, **bess_dispatch_yaml(battery, _definition().manifest)}
    )

    assert rebuilt is not None
    # The starting charge travels as kWh rounded to the watt-hour, so it returns to
    # within a hundredth of a percentage point; every other field returns exactly.
    assert rebuilt.initial_soc_pct == pytest.approx(battery.initial_soc_pct, abs=0.01)
    assert (
        dataclasses.replace(
            rebuilt, instance_id=battery.instance_id, initial_soc_pct=battery.initial_soc_pct
        )
        == battery
    )


def test_shed_yaml_inverts_load_shedding_config() -> None:
    panel = default_config()["panel_config"]
    shed = LoadSheddingConfig(soc_threshold_pct=37.0)

    panel["soc_shed_threshold"] = load_shedding_yaml(shed)["soc_shed_threshold"]

    assert load_shedding_config(panel) == shed


@pytest.mark.asyncio
async def test_a_masked_capture_imports(tmp_path: Path) -> None:
    """Upstream's capture masks by default, so the panel's id is not its serial."""
    retained = await capture_retained(DEFAULT_CONFIG)
    definition, _notes = definition_from_tree(
        tree_from_retained({t: p.decode() for t, p in retained.items()}), variant="span"
    )
    assert definition.manifest.of_class("panel")[0].instance_id == "masked-panel"

    imported = config_from_definition(definition)

    validate_yaml_config(imported)
    await _runs(imported, tmp_path)


@pytest.fixture
def dashboard_app(tmp_path: Path) -> web.Application:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    write_config(cfg_dir / "panel.yaml", default_config())
    ctx = DashboardContext(
        config_dir=cfg_dir,
        config_filter="panel.yaml",
        get_panel_configs=lambda: {},
        get_panel_ports=lambda: {},
        request_reload=lambda: None,
    )
    return create_dashboard_app(ctx)


async def _upload(app: web.Application, text: str) -> tuple[int, str]:
    form = FormData()
    form.add_field("file", text.encode(), filename="upload.yaml")
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/import", data=form)
        return resp.status, await resp.text()


@pytest.mark.asyncio
async def test_the_import_endpoint_takes_a_definition(
    dashboard_app: web.Application, tmp_path: Path
) -> None:
    path = tmp_path / "upload.definition.yaml"
    dump_definition(_definition(), path)

    status, _body = await _upload(dashboard_app, path.read_text(encoding="utf-8"))

    assert status == 200
    store = dashboard_app[APP_KEY_STORE]
    assert store.get_panel_config()["serial_number"].endswith("-clone")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["config", "definition"])
async def test_an_import_is_unsaved_until_saved(
    dashboard_app: web.Application, tmp_path: Path, kind: str
) -> None:
    """The imported config exists nowhere on disk, so leaving it must prompt."""
    if kind == "config":
        text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    else:
        path = tmp_path / "upload.definition.yaml"
        dump_definition(_definition(), path)
        text = path.read_text(encoding="utf-8")

    status, _body = await _upload(dashboard_app, text)

    assert status == 200
    assert dashboard_app[APP_KEY_STORE].dirty is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("schema: panel-sim-definition/1\nvariant: span\ndevices: []\n", "devices"),
        ("schema: panel-sim-definition/9\ndevices: []\n", "schema"),
    ],
)
async def test_a_bad_definition_is_a_400(
    dashboard_app: web.Application, text: str, names: str
) -> None:
    status, body = await _upload(dashboard_app, text)

    assert status == 400
    assert names in body
    # Upstream's loader reads a file, so the import hands it a scratch copy; the
    # message must name the upload, not where that copy lived on the server.
    assert tempfile.gettempdir() not in body


@pytest.mark.asyncio
async def test_a_pre_202639_definition_with_a_commissioned_circuit_is_a_400(
    dashboard_app: web.Application, tmp_path: Path
) -> None:
    """Deliberate, not a gap: SPAN locks these circuits only from release 202639.

    The definition renders and the clone reads the lock off the wire, so the
    imported config pairs `commissioned_system` with an earlier `firmware_version`.
    Validation refuses that pair (spec D18), and the endpoint says why, rather than
    import a lock the definition's own release never published.
    """
    config = default_config()
    solar = config["circuit_templates"]["solar"]
    solar["priority"] = "NEVER"
    solar["relay_behavior"] = "non_controllable"
    solar["commissioned_system"] = "pv"
    for circuit in config["circuits"]:
        if circuit["id"] == "solar_inverter":
            circuit["name"] = "Commissioned PV System"
    definition = build_definition(config)
    [panel] = definition.manifest.of_class("panel")
    earlier = dataclasses.replace(
        panel, metadata={**panel.metadata, "firmware-version": EARLIER_FIRMWARE}
    )
    manifest = DeviceManifest(
        instances=tuple(earlier if i is panel else i for i in definition.manifest.instances)
    )
    path = tmp_path / "earlier.definition.yaml"
    dump_definition(dataclasses.replace(definition, manifest=manifest), path)

    status, body = await _upload(dashboard_app, path.read_text(encoding="utf-8"))

    assert status == 400
    assert "locked only from SPAN release 202639" in body
