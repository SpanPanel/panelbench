"""Parent/child Homie wire publisher with native-device runtime.

Publishes the eBus **parent/child (v1.0)** device tree: every circuit, BESS, PV, EVSE, lugs
and MID is its own Homie device with its own ``$description`` and ``$state``, rather than a
namespaced node hanging off the panel. Topics are
``ebus/5/<device>/<capability>/<property>``.

Provenance, in two steps:

- Originally vendored from ``ebus-emitter``
  (https://github.com/electrification-bus/simulator) at commit ``5b84de8``, version 0.2.1 —
  the release that corrected the circuit energy reference frame. MIT licensed, same
  copyright holders as this repository. That copy published the **flat** schema.
- Re-vendored onto the parent/child schema (2026-08-03). The schema data came from the eBus
  specification and from panel-sim: 15 capability catalogs, 7 base profiles plus the SPAN
  overlay, 7 placement descriptors, and the MID device class. ``profile_loader`` and
  ``bag_builder`` are adopted from panel-sim with import paths rewritten; ``graph_builder``
  was ported to the SDK tree API.

**This copy does not track upstream, by design.** Upstream changes are not ported; fix bugs
here. Note the reason is *not* a schema disagreement — an earlier version of this docstring
said the two diverged because upstream moved to parent/child while we stayed flat, which was
true then and is not now. We followed them onto parent/child. What remains is an ordinary
permanent fork: the code carries SPAN-specific behaviour and its own test suite.

The flat schema still ships from the ``main`` branch of this repository, for SPAN firmware
that speaks it.

Why vendored rather than depended on:

- ``ebus-emitter`` is not published to PyPI, so the HA add-on image had no way to install
  it — ``pip install .`` in the Dockerfile silently produced an image that failed at
  startup with ``ModuleNotFoundError: No module named 'ebus_emitter'``.
- The previous arrangement (editable install from ``EBUS_EMITTER_PATH``) worked only for
  developers who had run ``scripts/dev-setup.sh``, and left a cross-repo version skew that
  nothing enforced.
- Because the fork is permanent, an external dependency delivered no upstream changes while
  costing path configuration, stale editable metadata, and a distribution problem for a
  package that will never be published.

It also collapses a real correctness hazard: ``clone.py`` seeds energy accumulators against
what this code publishes. While they lived in separate repos each side could look locally
correct while jointly inverting circuit energy — which is exactly what happened, and what
no single test suite could see. Both ends now sit in one repo under one test run.

The vendored catalogs under ``wire/catalogs/`` are byte-compared against the specification
by ``scripts/check-spec-provenance.py`` and must never be hand-edited; SPAN divergences go
in the ``wire/profiles/span/`` overlay. What this package actually publishes is checked
against those catalogs by ``scripts/check-conformance.py``. See ``DEVELOPER.md`` §Spec
Conformance.

Architecture:

- **Wire layer** (``wire/``): vendored capability catalogs, Homie 5 device profiles and
  mapping descriptors, graph builder, lifecycle controller, /set router, property bag diff
  cache, SDK seam.
- **Native devices** (``native_devices/``): emitter-resident, configured-and-self-driving
  device runtimes (BESS dispatch, load shedding).
- **Manifest physics** (``manifest_physics.py``): typed accessor over
  ``DeviceInstance.metadata`` for physics-relevant fields (voltage, breaker rating,
  tabs/legs, placement, default priority, relay behaviour).
- **Tick pipeline** (``relay_resolver.py`` + ``energy_integrator.py`` + ``panel_meter.py``
  + ``conventions/tab_legs.py``): per-tick state machinery the emitter uses to
  resolve circuit relay state, integrate energy, derive per-leg currents, and
  aggregate panel-level fields.

Producer contract: build a ``DeviceManifest`` once at startup, then call
``Emitter.publish_tick(TickInputs)`` each tick with signed circuit/EVSE powers,
``current_time``, and ``grid_online``. The emitter does the rest.

The tree is built **transport-free**: no ``mqtt_cfg`` is passed, so the SDK opens no socket
and still composes the whole tree. This simulator owns its transport and publishes through
its own client, so the SDK device tree is a naming and description model only."""

from span_panel_simulator.ebus_emitter.conventions.tab_legs import Leg, legs_for_tabs
from span_panel_simulator.ebus_emitter.emitter import Emitter
from span_panel_simulator.ebus_emitter.exceptions import (
    EmitterError,
    EmitterStateError,
    ManifestValidationError,
    MissingSetterError,
    ProfileValidationError,
    RuntimeSpecValidationError,
)
from span_panel_simulator.ebus_emitter.manifest import DeviceInstance, DeviceManifest
from span_panel_simulator.ebus_emitter.manifest_physics import (
    BessPhysics,
    CircuitPhysics,
    EvsePhysics,
    LugsPhysics,
    ManifestPhysicsView,
    PanelPhysics,
    PvPhysics,
)
from span_panel_simulator.ebus_emitter.native_devices import (
    BESSConfig,
    BESSDevice,
    ChargeMode,
    LoadSheddingConfig,
    LoadSheddingDevice,
    NativeDevice,
    NativeTickContext,
)
from span_panel_simulator.ebus_emitter.relay_resolver import (
    RelayRequester,
    RelayResolver,
    RelayState,
)
from span_panel_simulator.ebus_emitter.snapshot import (
    EbusBatterySnapshot,
    EbusCircuitSnapshot,
    EbusEvseSnapshot,
    EbusLugsSnapshot,
    EbusPanelDoor,
    EbusPanelInfo,
    EbusPanelMeter,
    EbusPanelPcs,
    EbusPanelPowerFlows,
    EbusPanelSnapshot,
    EbusPanelStatus,
    EbusPvSnapshot,
)
from span_panel_simulator.ebus_emitter.tick_inputs import PanelEnvelopeTick, TickInputs
from span_panel_simulator.ebus_emitter.wire.set_router import SetterHandler, SetterRegistry

__all__ = [
    "BESSConfig",
    "BESSDevice",
    "BessPhysics",
    "ChargeMode",
    "CircuitPhysics",
    "DeviceInstance",
    "DeviceManifest",
    "EbusBatterySnapshot",
    "EbusCircuitSnapshot",
    "EbusEvseSnapshot",
    "EbusLugsSnapshot",
    "EbusPanelDoor",
    "EbusPanelInfo",
    "EbusPanelMeter",
    "EbusPanelPcs",
    "EbusPanelPowerFlows",
    "EbusPanelSnapshot",
    "EbusPanelStatus",
    "EbusPvSnapshot",
    "Emitter",
    "EmitterError",
    "EmitterStateError",
    "EvsePhysics",
    "Leg",
    "LoadSheddingConfig",
    "LoadSheddingDevice",
    "LugsPhysics",
    "ManifestPhysicsView",
    "ManifestValidationError",
    "MissingSetterError",
    "NativeDevice",
    "NativeTickContext",
    "PanelEnvelopeTick",
    "PanelPhysics",
    "ProfileValidationError",
    "PvPhysics",
    "RelayRequester",
    "RelayResolver",
    "RelayState",
    "RuntimeSpecValidationError",
    "SetterHandler",
    "SetterRegistry",
    "TickInputs",
    "legs_for_tabs",
]
