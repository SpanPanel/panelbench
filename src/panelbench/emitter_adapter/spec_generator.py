"""Build a ``DeviceManifest`` from a loaded clone-profile dict.

The manifest carries identity + physics keys per the v0.3.0 emitter contract.
The emitter parses physics fields via ``ManifestPhysicsView`` at construction
and uses them for relay-state ownership, energy integration, panel-meter
aggregation, and per-leg current calculation.

``build_manifest`` is a thin orchestrator: it asks each ``_xxx_instance(s)``
helper to build its slice of the manifest and concatenates the results.
Adding a new device class (e.g. MID, second BESS) is a single ``append``
on the orchestrator and a new helper — no need to touch the rest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebus_panel_sim import DeviceInstance, DeviceManifest

from panelbench.emitter_adapter.instance_ids import (
    bess_device_id,
    evse_circuit_serial,
    evse_device_id,
    evse_serial_number,
    lugs_device_ids,
    mid_device_id,
    pv_device_id,
    pv_inverter_device_id,
    stable_circuit_uuid,
)
from panelbench.firmware import SPAN_RELEASE_202639, panel_firmware_version, predates
from panelbench.hardware import panel_hardware_version
from panelbench.inverter import (
    normalise_inverter_type,
    template_inverter_type,
)
from panelbench.panel_models import PANEL_SIZE_TO_MODEL

if TYPE_CHECKING:
    from collections.abc import Mapping

    from panelbench.config_types import (
        CircuitDefinitionExtended,
        CircuitTemplateExtended,
        EVSEConfigYAML,
        PVConfigYAML,
        SimulationConfig,
    )

# Allowed Homie-convention values for circuit relay-behavior — dash-form on the
# wire.  YAML clones written before the dash convention may still use underscore
# form, so we normalise once before validating.  Inverter type follows the same
# rule but lives in ``inverter``, because the engine and the dashboard have to
# agree with this module on it.
_VALID_RELAY_BEHAVIORS = frozenset({"controllable", "non-controllable", "always-on"})


def normalise_relay_behavior(raw: str) -> str:
    """Coerce ``controllable`` / ``non_controllable`` / ``always_on`` (any
    underscore-vs-dash form) to the dashed Homie convention; default to
    ``controllable`` when unrecognised."""
    candidate = raw.lower().replace("_", "-")
    return candidate if candidate in _VALID_RELAY_BEHAVIORS else "controllable"


def relay_locked(relay_behavior: str) -> bool:
    """Whether a circuit declaring this ``relay_behavior`` has a locked relay.

    The producer-side mirror of ``ebus_panel_sim.manifest_physics.relay_locked``,
    which is what actually decides the published tree: both non-controllable
    spellings are one commissioning flag on the hardware this models, and SPAN
    publishes ``relay-controllable = !always-on``. A locked relay carries no
    ``$settable`` on ``switch/relay``, accepts no ``/set``, is never load-shed,
    and reports ``relay-requester = CONFIGURATION`` at rest
    (``capabilities/switch.md:28-29``).

    Here so that anything offering the user a relay command asks the same
    question the emitter asks before publishing the offer. Takes the config's
    own ``relay_behavior`` spelling rather than manifest metadata, because the
    callers that need it hold a config, not a built manifest.
    """
    return normalise_relay_behavior(relay_behavior) != "controllable"


def build_manifest(profile: SimulationConfig) -> DeviceManifest:
    """Walk the loaded SimulationConfig dict; emit a DeviceManifest the emitter
    consumes. Identity + physics — no behaviour, no schedule, no modelling."""
    instances: list[DeviceInstance] = [
        _panel_instance(profile),
        *_lugs_instances(profile),
        *_circuit_instances(profile),
    ]
    bess = _bess_instance(profile)
    if bess is not None:
        instances.append(bess)
    instances.extend(_pv_instances(profile))
    instances.extend(_evse_instances(profile))
    mid = _mid_instance(profile)
    if mid is not None:
        instances.append(mid)
    return DeviceManifest(instances=tuple(instances))


def _panel_instance(profile: SimulationConfig) -> DeviceInstance:
    panel_cfg = profile["panel_config"]
    panel_id = panel_cfg["serial_number"]
    panel_size = int(panel_cfg.get("total_tabs", 40))
    panel_model = PANEL_SIZE_TO_MODEL.get(panel_size, f"MAIN_{panel_size}")
    return DeviceInstance(
        entity_class="panel",
        instance_id=panel_id,
        display_name=panel_cfg.get("display_name", "Span Panel"),
        metadata={
            "vendor-name": "Span",
            "serial-number": panel_id,
            "firmware-version": panel_firmware_version(profile),
            "hardware-version": panel_hardware_version(profile),
            "panel-size": str(panel_size),
            "main-breaker-rating-a": str(int(panel_cfg.get("main_size", 200))),
            "panel-model": panel_model,
            "postal-code": str(panel_cfg.get("postal_code", "94103")),
            "time-zone": str(panel_cfg.get("time_zone", "America/Los_Angeles")),
            "service-voltage-v": str(panel_cfg.get("service_voltage_v", 240.0)),
            "line-voltage-v": str(panel_cfg.get("line_voltage_v", 120.0)),
            "islandable": "true" if _islandable(profile) else "false",
        },
    )


def _lugs_instances(profile: SimulationConfig) -> list[DeviceInstance]:
    upstream_id, downstream_id = lugs_device_ids(profile["panel_config"]["serial_number"])
    return [
        DeviceInstance(
            entity_class="lugs",
            instance_id=upstream_id,
            display_name="Upstream lugs",
            metadata={"direction": "upstream"},
        ),
        DeviceInstance(
            entity_class="lugs",
            instance_id=downstream_id,
            display_name="Downstream lugs",
            metadata={"direction": "downstream"},
        ),
    ]


def _circuit_instances(profile: SimulationConfig) -> list[DeviceInstance]:
    templates = profile.get("circuit_templates") or {}
    panel_id = profile["panel_config"]["serial_number"]
    instances: list[DeviceInstance] = []
    for idx, c in enumerate(profile.get("circuits") or [], start=1):
        tabs = c.get("tabs") or [0]
        template_name = c.get("template", "")
        template: CircuitTemplateExtended | None = templates.get(template_name)
        commissioned_system: str | None
        if template is None:
            relay_behavior_raw = "controllable"
            priority = "NICE_TO_HAVE"
            breaker_rating = 20.0
            never_backup = False
            commissioned_system = None
        else:
            relay_behavior_raw = str(template.get("relay_behavior", "controllable"))
            priority = str(template.get("priority", "NICE_TO_HAVE")).upper()
            never_backup = bool(template.get("never_backup", False))
            commissioned_system = template.get("commissioned_system")
            # ``breaker_rating_a`` is the producer-side legacy key; the typed
            # ``breaker_rating`` (no units suffix) is the canonical YAML field.
            # Read both so manifests built from older clones still work.
            breaker_rating = float(
                template.get("breaker_rating_a") or template.get("breaker_rating", 20),
            )
        relay_behavior = normalise_relay_behavior(relay_behavior_raw)
        instances.append(
            DeviceInstance(
                entity_class="circuit",
                instance_id=stable_circuit_uuid(panel_id, c["id"]),
                display_name=c.get("name", c["id"]),
                metadata={
                    "tab-numbers": ",".join(str(int(t)) for t in tabs if t),
                    "breaker-rating-a": str(breaker_rating),
                    "default-priority": priority,
                    "relay-behavior": relay_behavior,
                    # A main panel's circuits sit on its busbar, so `upstream-of-lugs`
                    # is the only placement any config here models. `downstream-of-lugs`
                    # means the load is past the feedthrough, which the spec puts in a
                    # *sub-enclosure* — its own device with its own circuits — not in
                    # this panel's circuit list (distribution-enclosure.md 0.12, and
                    # electrification-bus/distribution-enclosure-simulator#30).
                    #
                    # The old default was `downstream-of-lugs`, inherited from upstream's
                    # example. It put every circuit past the feedthrough, which makes the
                    # feedthrough sum equal the whole panel and the two lugs devices
                    # publish byte-identical meters — indistinguishable, so a mapping that
                    # swapped them read the same. `test_lugs_are_distinguishable.py` is the
                    # guard; this default is only what keeps configs terse.
                    "placement": str(c.get("placement", "upstream-of-lugs")),
                    "always-on": "true" if relay_behavior == "always-on" else "false",
                    "pcs-priority": str(c.get("pcs_priority", idx)),
                    # The second commissioning lock, independent of the relay one:
                    # `load-shed/priority` publishes with `$settable = !never-backup`, so
                    # a locked circuit offers a consumer no way to re-prioritise it. Set
                    # only when true — `manifest_physics.never_backup` reads an absent key
                    # as unlocked, and the emitter rejects the key on any circuit whose
                    # `default-priority` is not `OFF_GRID`, so writing a blanket "false"
                    # would add a key to every manifest that changes nothing. Contrast
                    # `always-on` above, which the relay predicate ORs and so must always
                    # be written.
                    **({"never-backup": "true"} if never_backup else {}),
                    # The emitter locks the relay and fixes the priority at NEVER from
                    # this key alone; validation has already required the config to say
                    # both, so the published tree and the config cannot disagree.
                    **(
                        {"commissioned-system": commissioned_system} if commissioned_system else {}
                    ),
                },
            ),
        )
    return instances


def _bess_instance(profile: SimulationConfig) -> DeviceInstance | None:
    bess_cfg = profile.get("bess") or {}
    if not bess_cfg.get("enabled"):
        return None
    bess_meta: dict[str, str] = {
        "vendor-name": str(bess_cfg.get("vendor", "Span")),
        "nameplate-capacity-kwh": str(bess_cfg.get("nameplate_capacity_kwh", 13.5)),
        "relative-position": str(bess_cfg.get("relative_position", "UPSTREAM")),
    }
    model = bess_cfg.get("model") or bess_cfg.get("product_name")
    if model is not None:
        bess_meta["model"] = str(model)
    if "part_number" in bess_cfg:
        bess_meta["part-number"] = str(bess_cfg["part_number"])
    if "serial_number" in bess_cfg:
        bess_meta["serial-number"] = str(bess_cfg["serial_number"])
    if "firmware_version" in bess_cfg:
        bess_meta["firmware-version"] = str(bess_cfg["firmware_version"])
    if "feed" in bess_cfg:
        bess_meta["feed"] = str(bess_cfg["feed"])
    if "initial_soe_kwh" in bess_cfg:
        bess_meta["initial-soe-kwh"] = str(bess_cfg["initial_soe_kwh"])
    return DeviceInstance(
        entity_class="bess",
        instance_id=bess_device_id(profile["panel_config"]["serial_number"], bess_cfg),
        display_name="Battery",
        metadata=bess_meta,
    )


def _mid_instance(profile: SimulationConfig) -> DeviceInstance | None:
    """The Microgrid Interconnect Device a commissioned BESS brings with it.

    v1.0 introduced this: *every* BESS child publishes a MID carrying the grid and
    islanding state, and where the hardware presents no separable MID the panel
    synthesizes one. It is the islanding authority, and it is where
    ``grid-forming-entity`` and ``islanding-state`` live — the successors to the
    flat schema's ``dominant-power-source``.

    Gated on a BESS that forms a premises-wiring island, because a MID that cannot
    island has nothing to be the authority over. See ``_bess_is_grid_forming`` for
    why the battery decides that and not the PV inverter.

    Placement is not set here. ``wire/mapping/mid.yaml`` declares it
    ``child-of-parent`` with ``parent_entity_class: bess``, so the graph builder
    parents it under the battery and the panel sees it as a grandchild — which is
    what real r202633 firmware publishes, verified live by eBus. The device id
    follows ``<bess-id>-mid`` from the migration guide's stability table; on real
    firmware the BESS id is itself ``<proxier>-<serial>``, which is why the same
    rule yields ``<panel>-<bess>-mid`` there and a shorter id here.

    Every metadata key is optional to the emitter, so this publishes only what the
    config actually knows rather than inventing identity for a synthesized device.
    """
    bess_cfg = profile.get("bess") or {}
    if not bess_cfg.get("enabled") or not _bess_is_grid_forming(profile):
        return None

    metadata = {"vendor-name": str(bess_cfg.get("vendor", "Span"))}
    serial = bess_cfg.get("serial_number")
    if serial is not None:
        # Derived from the battery's serial, not invented: the MID is part of that
        # unit, so a consumer resolving one to the other should be able to.
        metadata["serial-number"] = f"{serial}-mid"
    mid_model = bess_cfg.get("mid_product_name")
    if mid_model is not None:
        metadata["model"] = str(mid_model)
    # Firmware and hardware revision are the MID's own, not the battery's, so they
    # get their own keys rather than reusing the BESS values. r202633 documents all
    # three of model/firmware-version/hardware-version on the MID's info node, and
    # they are what a consumer shows on the device card.
    mid_firmware = bess_cfg.get("mid_firmware_version")
    if mid_firmware is not None:
        metadata["firmware-version"] = str(mid_firmware)
    mid_hardware = bess_cfg.get("mid_hardware_version")
    if mid_hardware is not None:
        metadata["hardware-version"] = str(mid_hardware)
    return DeviceInstance(
        entity_class="mid",
        instance_id=mid_device_id(profile["panel_config"]["serial_number"], bess_cfg),
        display_name="Microgrid Interconnect Device",
        metadata=metadata,
    )


def pv_section_circuit(profile: SimulationConfig) -> CircuitDefinitionExtended | None:
    """The PV circuit feeding the inverter the top-level ``pv`` section describes.

    A real panel publishes the inverter its feeding circuit names in
    ``connection/feeds-device-id``, so the section's identity follows a circuit,
    never a position in the list: the one ``pv.feed`` names, else the panel's only
    PV circuit. ``pv.feed`` names a circuit by its ``id`` under ``circuits``. Not by
    the device id the circuit publishes, which follows the serial the engine settles
    on after validation (a ``sim-`` prefix, or an override), so a config cannot rely
    on it.

    With several PV circuits and no ``pv.feed`` the section describes none of them,
    and each inverter is named by its own circuit. A panel before SPAN release
    202639 publishes one PV device for all of them, fed by one circuit
    (SPAN-API-Client-Docs CHANGELOG, Release 202639), so a config naming such a
    release must say which.

    Raises:
        ValueError: ``pv.feed`` names no PV circuit, or a config naming a release
            before 202639 has several PV circuits and no ``pv.feed``.
    """
    pv_circuits = _circuits_for_device_type(profile, "pv")
    pv_cfg = profile.get("pv") or {}
    if "feed" in pv_cfg:
        return _pv_circuit_named(profile, pv_circuits, str(pv_cfg["feed"]))
    if len(pv_circuits) == 1:
        return pv_circuits[0]
    firmware = panel_firmware_version(profile)
    if len(pv_circuits) > 1 and predates(firmware, SPAN_RELEASE_202639):
        ids = ", ".join(repr(circuit["id"]) for circuit in pv_circuits)
        raise ValueError(
            f"This panel has PV circuits {ids}, and firmware_version {firmware!r} names a "
            "release before 202639, which publishes one solar device fed by one of them: "
            "set pv.feed to the id of the circuit feeding the inverter the pv section describes"
        )
    return None


def _pv_circuit_named(
    profile: SimulationConfig, pv_circuits: list[CircuitDefinitionExtended], feed: str
) -> CircuitDefinitionExtended:
    """The PV circuit *feed* names by its config ``id``."""
    named = next(
        (circuit for circuit in profile.get("circuits") or [] if circuit["id"] == feed), None
    )
    if named is None:
        raise ValueError(
            f"pv.feed {feed!r} names no circuit: set it to the id of the PV circuit feeding "
            "the inverter the pv section describes"
        )
    if not any(circuit is named for circuit in pv_circuits):
        raise ValueError(
            f"pv.feed names circuit {named['id']!r}, which is not a PV circuit: its template "
            "has no device_type: pv"
        )
    return named


def _pv_instance(profile: SimulationConfig) -> DeviceInstance | None:
    """The single PV device this panel has always published, fed by ``pv_section_circuit``."""
    pv_cfg = profile.get("pv") or {}
    circuit = pv_section_circuit(profile)
    if not pv_cfg.get("enabled") and circuit is None:
        return None
    relative_position = pv_cfg.get("relative_position")
    if relative_position is None:
        relative_position = "IN_PANEL" if circuit is not None else "UPSTREAM"
    templates = profile.get("circuit_templates") or {}
    # With no PV circuit -- an inverter upstream of the panel, or a config written
    # before templates carried `device_type` -- the first producer's template is the
    # only one there is to read the inverter's type from.
    inverter_template = (
        templates.get(circuit["template"])
        if circuit is not None
        else _first_producer_template(profile)
    )
    instance_id = pv_device_id(profile["panel_config"]["serial_number"], pv_cfg)
    return DeviceInstance(
        entity_class="pv",
        instance_id=instance_id,
        display_name=instance_id,
        metadata=_pv_metadata(
            profile,
            circuit,
            pv_cfg,
            feed=_circuit_device_id(profile, circuit),
            inverter_template=inverter_template or {},
            relative_position=str(relative_position),
        ),
    )


def _inverter_identity(
    circuit: CircuitDefinitionExtended | None, defaults: PVConfigYAML
) -> dict[str, str]:
    """The ``info`` metadata naming the inverter *circuit* feeds.

    The circuit's own values first, because a DER's identity belongs to the circuit
    that feeds it; then *defaults*, the top-level ``pv`` section, which describes the
    inverter ``pv_section_circuit`` binds it to, as it did before circuits could
    carry one.
    """
    own_vendor = circuit.get("vendor") if circuit is not None else None
    own_model = circuit.get("model") if circuit is not None else None
    own_serial = circuit.get("serial_number") if circuit is not None else None
    # `str()` as `_pv_instance` always applied it: YAML may hand a number for a
    # model or serial written without quotes.
    identity = {"vendor-name": str(own_vendor or defaults.get("vendor", "Enphase"))}
    model = own_model or defaults.get("product_name")
    if model:
        identity["model"] = str(model)
    serial = own_serial or defaults.get("serial_number")
    if serial:
        identity["serial-number"] = str(serial)
    return identity


def _pv_metadata(
    profile: SimulationConfig,
    circuit: CircuitDefinitionExtended | None,
    defaults: PVConfigYAML,
    *,
    feed: str | None,
    inverter_template: Mapping[str, object],
    relative_position: str,
) -> dict[str, str]:
    """One inverter's manifest metadata, for the single device and for each of several.

    *defaults* is the top-level ``pv`` section for the inverter ``pv_section_circuit``
    binds it to and empty for the others. Its declared inverter type wins over
    *inverter_template*'s, and its nameplate over the circuit template's. The
    firmware version is not read from *defaults*: see ``_circuit_firmware``.
    """
    declared_inverter = defaults.get("inverter_type")
    inverter_type = (
        normalise_inverter_type(str(declared_inverter))
        if declared_inverter is not None
        else template_inverter_type(inverter_template)
    )
    nameplate = defaults.get("nameplate_capacity_w") or _circuit_nameplate_w(
        profile, circuit, 5000.0
    )
    metadata = {
        **_inverter_identity(circuit, defaults),
        "nominal-power-w": str(nameplate),
        "inverter-type": inverter_type,
        "relative-position": relative_position,
    }
    firmware = _circuit_firmware(circuit, profile.get("pv") or {})
    if firmware is not None:
        metadata["firmware-version"] = firmware
    if feed is not None:
        metadata["feed"] = feed
    return metadata


def _circuit_firmware(
    circuit: CircuitDefinitionExtended | None, section: Mapping[str, object]
) -> str | None:
    """The firmware version of the device *circuit* feeds, an inverter or a drive.

    The circuit's own first, then *section*'s, the top-level ``pv`` or ``evse``
    section, for every such device, not only the first: a firmware version is not
    identity, and a device that declares ``info/firmware-version`` without a value
    leaves a consumer's entity waiting forever.
    """
    own = circuit.get("firmware_version") if circuit is not None else None
    if own:
        return str(own)
    return str(section["firmware_version"]) if "firmware_version" in section else None


def _pv_instances(profile: SimulationConfig) -> list[DeviceInstance]:
    """One PV device per inverter, or the single device this panel has always published.

    Several devices for a panel with two or more PV circuits from SPAN release
    202639, or naming no release. Otherwise ``_pv_instance``'s one device: a panel
    with one inverter keeps its id under every firmware, and a panel before 202639
    published one aggregate device fed by one PV circuit, ``pv_section_circuit``
    (SPAN-API-Client-Docs CHANGELOG, Release 202639).

    Every PV device is named after its own device id, as SPAN firmware names an
    inverter (both public MAIN 32 captures, r202633 and r202639), so no name
    follows a circuit's place in the list.
    """
    circuits = _circuits_for_device_type(profile, "pv")
    if len(circuits) < 2 or predates(panel_firmware_version(profile), SPAN_RELEASE_202639):
        single = _pv_instance(profile)
        return [] if single is None else [single]
    panel_id = profile["panel_config"]["serial_number"]
    pv_cfg = profile.get("pv") or {}
    section_circuit = pv_section_circuit(profile)
    templates = profile.get("circuit_templates") or {}
    instances: list[DeviceInstance] = []
    for circuit in circuits:
        # The `pv` section describes one inverter, as it did when a panel had one.
        defaults: PVConfigYAML = pv_cfg if circuit is section_circuit else {}
        circuit_id = stable_circuit_uuid(panel_id, circuit["id"])
        metadata = _pv_metadata(
            profile,
            circuit,
            defaults,
            feed=circuit_id,
            inverter_template=templates.get(circuit["template"]) or {},
            relative_position=str(defaults.get("relative_position", "IN_PANEL")),
        )
        identifier = metadata.get("serial-number") or metadata.get("model")
        instance_id = pv_inverter_device_id(panel_id, identifier, circuit_id)
        instances.append(
            DeviceInstance(
                entity_class="pv",
                instance_id=instance_id,
                display_name=instance_id,
                metadata=metadata,
            )
        )
    return instances


def _evse_instances(profile: SimulationConfig) -> list[DeviceInstance]:
    evse_cfg = profile.get("evse") or {}
    panel_id = profile["panel_config"]["serial_number"]
    feed_circuits = _circuits_for_device_type(profile, "evse")
    explicit_feed = str(evse_cfg["feed"]) if "feed" in evse_cfg else None
    if explicit_feed:
        feeds = [explicit_feed]
    else:
        feeds = [stable_circuit_uuid(panel_id, circuit["id"]) for circuit in feed_circuits]
    if not feeds and evse_cfg.get("enabled"):
        feeds = [""]
    if not feeds:
        return []

    instances: list[DeviceInstance] = []
    for idx, feed in enumerate(feeds, start=1):
        # `feed_circuits` is the same list `feeds` was built from, in the same order,
        # so index `idx-1` is this instance's circuit -- except on the explicit-feed
        # path, which pins a single feed by id and has no circuit to read.
        circuit = (
            feed_circuits[idx - 1] if not explicit_feed and idx <= len(feed_circuits) else None
        )
        instances.append(
            DeviceInstance(
                entity_class="evse",
                instance_id=evse_device_id(panel_id, evse_cfg, idx),
                display_name=_evse_display_name(feed_circuits, idx),
                metadata=_evse_metadata(panel_id, circuit, evse_cfg, idx=idx, feed=feed),
            ),
        )
    return instances


def _evse_metadata(
    panel_id: str,
    circuit: CircuitDefinitionExtended | None,
    evse_cfg: EVSEConfigYAML,
    *,
    idx: int,
    feed: str,
) -> dict[str, str]:
    """One drive's manifest metadata: the ``evse`` section's, then its circuit's own.

    The circuit's serial wins over the panel-and-position derivation, so a config
    that pins identity is immune to circuit reordering. Its firmware version wins
    over the ``evse`` section's, which is every drive's default: see
    ``_circuit_firmware``.
    """
    metadata = {
        "vendor-name": str(evse_cfg.get("vendor", "SPAN")),
        "model": str(evse_cfg.get("product", "SPAN Drive")),
        "part-number": str(evse_cfg.get("part_number", "SPN-DRV-001")),
        "firmware-version": _circuit_firmware(circuit, evse_cfg) or "sim/v0.1.0",
        "max-current-a": str(evse_cfg.get("max_current_a", 32.0)),
        "serial-number": evse_circuit_serial(circuit)
        or evse_serial_number(evse_cfg, panel_id, idx),
    }
    if feed:
        metadata["feed"] = feed
    return metadata


def _bess_is_grid_forming(profile: SimulationConfig) -> bool:
    """Whether the battery forms a premises-wiring island — which is what a MID means.

    `devices/bess.md` classifies on the capability set rather than on any declared
    type: "a MID `grid` child means premises-segment backup ... neither means no
    backup", and "a premises-wiring grid-forming BESS publisher MUST include a MID
    child device". So the MID follows the *battery*, and this is the property that
    decides whether one exists.

    It used to be decided by the PV inverter. Those agree for a hybrid-inverter site,
    which is why the MID appeared at all, and they diverge for the cases that matter
    most: a grid-forming BESS with AC-coupled PV, and a grid-forming BESS with no PV
    whatsoever -- the canonical residential backup product, which published a battery
    and no MID at all.

    A commissioned battery now DEFAULTS to grid-forming. The previous default claimed
    less with nothing declared, which reads as the careful choice and was not: the
    configs that declare nothing are the overwhelming majority -- everything the flat
    simulator wrote, every clone taken before the key existed, and two of this
    repository's own shipped defaults -- so the cautious default silently withheld the
    MID from almost every real config, and a consumer had no islanding state and no
    error explaining why. Claiming less is only honest when the claim is uncertain;
    a commissioned BESS in a SPAN enclosure forms a premises island.

    Resolution order:

    0. No commissioned battery, no island. Nothing else is consulted, because there is
       no grid-forming source for the other answers to be about -- a panel that
       inherited ``islandable: true`` and has no BESS is claiming a capability it
       cannot have. This also keeps the widened default from reaching the *panel*
       metadata key, which unlike the MID gate has no battery check of its own.
    1. ``bess.grid_forming`` — says the thing directly, in both directions. A
       grid-following battery still says so and still publishes no MID.
    2. ``panel_config.islandable`` — the flat schema's panel-level boolean, which v1.0
       retires in favour of MID presence. Honoured so an existing clone that set it
       false keeps meaning what it said, and not written by anything new.
    3. Otherwise the battery forms an island.

    The hybrid-PV inference this used to end on is gone. It could only ever answer
    "yes", which the default now covers, and answering "no" for a non-hybrid inverter
    is exactly the reading that made a battery beside AC-coupled solar lose its MID.
    """
    bess_cfg = profile.get("bess") or {}
    if not bess_cfg.get("enabled"):
        return False

    if "grid_forming" in bess_cfg:
        return bool(bess_cfg["grid_forming"])

    panel_cfg = profile["panel_config"]
    if "islandable" in panel_cfg:
        return bool(panel_cfg["islandable"])

    return True


def _islandable(profile: SimulationConfig) -> bool:
    """Whether this enclosure participates in a premises-wiring island.

    Identical to the battery being grid-forming, because that battery is what forms
    the island. Kept as a separate name because the *panel* metadata key is about the
    enclosure and the MID gate is about the BESS; they coincide today and would not if
    a standalone MID ever appeared (see `bess.md`, "an architecturally valid
    alternative ... is not yet present in commercial products").
    """
    return _bess_is_grid_forming(profile)


def _first_producer_template(profile: SimulationConfig) -> CircuitTemplateExtended | None:
    """The template of the first producer circuit, read for a PV device with no PV circuit."""
    templates = profile.get("circuit_templates") or {}
    for circuit in profile.get("circuits") or []:
        template = templates.get(circuit.get("template", ""))
        if template is None:
            continue
        energy_profile = template.get("energy_profile")
        if energy_profile is None:
            continue
        if str(energy_profile.get("mode", "")) == "producer":
            return template
    return None


def _circuit_device_id(
    profile: SimulationConfig, circuit: CircuitDefinitionExtended | None
) -> str | None:
    """The device id *circuit* publishes, which a DER it feeds names as its ``feed``."""
    if circuit is None:
        return None
    return stable_circuit_uuid(profile["panel_config"]["serial_number"], circuit["id"])


def _circuits_for_device_type(
    profile: SimulationConfig,
    device_type: str,
) -> list[CircuitDefinitionExtended]:
    templates = profile.get("circuit_templates") or {}
    circuits: list[CircuitDefinitionExtended] = []
    for circuit in profile.get("circuits") or []:
        template = templates.get(circuit.get("template", ""))
        if template is not None and template.get("device_type") == device_type:
            circuits.append(circuit)
    return circuits


def _evse_display_name(feed_circuits: list[CircuitDefinitionExtended], idx: int) -> str:
    if 0 <= idx - 1 < len(feed_circuits):
        return str(feed_circuits[idx - 1].get("name", "EV Charger"))
    return "EV Charger"


def _circuit_nameplate_w(
    profile: SimulationConfig, circuit: CircuitDefinitionExtended | None, default: float
) -> float:
    """The nameplate rating of the device *circuit* feeds, from its template."""
    if circuit is None:
        return default
    template = (profile.get("circuit_templates") or {}).get(circuit.get("template", ""))
    if template is None:
        return default
    energy_profile = template.get("energy_profile", {})
    return float(energy_profile.get("nameplate_capacity_w", default))
