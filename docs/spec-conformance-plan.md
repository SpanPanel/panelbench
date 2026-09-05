# Spec Conformance Checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a conformance profile of what the simulator publishes — classifying every property as match, divergence, extension or omission — and fail the
build on the small set of genuine eBus and Homie violations.

**Architecture:** A new `conformance` package parses Homie 5 `$description` documents into a typed tree, compares that tree against the vendored capability
catalogs and device profiles, and emits findings. The package imports nothing from `panelbench` and nothing MQTT-related, so the same rules run against an
in-process device tree in unit tests and against a captured retained-topic dump from a live broker.

**Tech Stack:** Python 3.14, dataclasses, `json` from the standard library, pytest. No new runtime dependencies.

**Design document:** [`docs/spec-conformance-design.md`](spec-conformance-design.md). Read it before Task 1 — particularly "The conformance floor", which
explains why only four rules fail a build and why a stricter checker would be wrong.

## Global Constraints

- Python floor is `>=3.14` (`pyproject.toml:9`); mypy runs `python_version = "3.14"`, ruff targets `py313`.
- `uv run mypy --strict src/panelbench/` must pass. **No `Any`, no `# type: ignore`.** `json.loads` returns `Any` — bind it to a variable annotated `object` and
  narrow with `isinstance`, as shown in Task 1.
- `src/panelbench/conformance/` must import nothing from `panelbench` and nothing MQTT-related (`ebus_sdk`, `aiomqtt`, `paho`). Task 8 adds the test that
  enforces this.
- Every module starts with `from __future__ import annotations`, matching the existing codebase.
- `uv run ruff check --fix src/ tests/` and `uv run ruff format src/ tests/` must be clean; pre-commit runs both plus `mypy --strict`.
- Never edit a file under `ebus_emitter/wire/catalogs/` — they are byte-compared against the specification by `scripts/check-spec-provenance.py` and an edit
  fails CI.
- Commit messages follow the existing conventional-commit style (`feat:`, `test:`, `fix:`, `docs:`). No attribution trailers of any kind.
- Tests live in `tests/conformance/`, mirroring the package layout as the existing `tests/ebus_emitter/` does.

## File Structure

| File                                            | Responsibility                                           |
| ----------------------------------------------- | -------------------------------------------------------- |
| `src/panelbench/conformance/__init__.py`        | Public API re-exports                                    |
| `src/panelbench/conformance/model.py`           | Parse `$description` documents into a typed tree         |
| `src/panelbench/conformance/catalogs.py`        | Load vendored capability catalogs; the abstract-unit set |
| `src/panelbench/conformance/device_profiles.py` | Load vendored device profiles                            |
| `src/panelbench/conformance/rules.py`           | `Finding` type; violations V1–V4; observations O1–O9     |
| `src/panelbench/conformance/report.py`          | Aggregate findings into a report; render text and JSON   |
| `src/panelbench/conformance/feeds.py`           | `from_devices`, `from_capture`                           |
| `scripts/check-conformance.py`                  | CLI, mirroring `scripts/check-spec-provenance.py`        |

Two separate notions of "profile" exist in this codebase. The eBus **device profile** (`wire/profiles/*.json`) says which capabilities a device type composes;
it is loaded by `device_profiles.py`. The **conformance report** is our output; it lives in `report.py`. The names are deliberately unalike to stop them being
confused.

## Reference shapes

Confirmed against `ebus_sdk` 0.17.0 and the vendored catalogs. Do not guess these.

`Device.description()`:

```python
{"homie": "5.0", "version": <int timestamp>, "type": str, "name": str,
 "nodes": {node_id: <node description>}, "children": [str, ...],
 "root": str, "parent": str,            # present only on a non-root device
 "extensions": [str, ...]}
```

`Node.description()`:

```python
{"name": str, "type": str, "properties": {prop_id: <property description>}}
```

`Property.description()` — keys are omitted rather than null when unset:

```python
{"name": str, "datatype": str,
 "format": str,        # only when set
 "settable": True,     # only when true
 "retained": False,    # only when NOT retained
 "unit": str}          # only when set
```

Capability catalog JSON (`wire/catalogs/soc.json`):

```python
{"$schema": ..., "schema_version": "property-schema-v1", "kind": "capability-catalog",
 "capability": "energy.ebus.capability.soc", "version": "0.1", "status": "DRAFT",
 "date": "2026-07-11",
 "properties": {"soc": {"datatype": "float", "unit": "%", "req": "MAY", "description": "..."}}}
```

**`version` in a device description is `Device.now_ems()`, a timestamp.** It changes on every call, so description documents are not byte-stable. The model does
not carry it; golden assertions are made against the report, never against raw documents.

---

### Task 1: Typed tree model

**Files:**

- Create: `src/panelbench/conformance/__init__.py`
- Create: `src/panelbench/conformance/model.py`
- Test: `tests/conformance/__init__.py`, `tests/conformance/test_model.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `HomieProperty`, `HomieNode`, `HomieDevice`, `HomieTree`, `DescriptionError`, `parse_device(device_id: str, raw: object) -> HomieDevice`,
  `build_tree(documents: dict[str, object]) -> HomieTree`.

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_model.py
from __future__ import annotations

import pytest

from panelbench.conformance.model import DescriptionError, build_tree, parse_device


def test_parse_device_reads_nodes_and_properties() -> None:
    raw = {
        "homie": "5.0", "version": 1, "type": "energy.ebus.device.bess", "name": "BESS",
        "nodes": {"soc": {"name": "State", "type": "energy.ebus.capability.soc",
                          "properties": {"soe": {"name": "State of energy",
                                                 "datatype": "float", "unit": "kWh"}}}},
        "children": [], "extensions": [],
    }
    device = parse_device("bess-1", raw)
    assert device.type == "energy.ebus.device.bess"
    prop = device.nodes["soc"].properties["soe"]
    assert prop.datatype == "float"
    assert prop.unit == "kWh"
    assert prop.settable is False
    assert prop.retained is True
    assert prop.format is None


def test_missing_datatype_is_a_description_error() -> None:
    raw = {"name": "d", "type": "t", "nodes": {"n": {"name": "n", "type": "t",
           "properties": {"p": {"name": "p"}}}}, "children": []}
    with pytest.raises(DescriptionError, match="datatype"):
        parse_device("d", raw)


def test_build_tree_indexes_by_device_id() -> None:
    doc = {"name": "d", "type": "t", "nodes": {}, "children": []}
    tree = build_tree({"d1": doc, "d2": doc})
    assert sorted(tree.devices) == ["d1", "d2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_model.py -v` Expected: FAIL with `ModuleNotFoundError: No module named 'panelbench.conformance'`

- [ ] **Step 3: Write the implementation**

Create `src/panelbench/conformance/__init__.py` as an empty file for now (Task 8 fills in the re-exports), then:

```python
# src/panelbench/conformance/model.py
"""Homie 5 $description documents parsed into a typed tree.

Knows the Homie document shape and nothing else: no eBus vocabulary, no catalogs,
no transport. Everything downstream reads this model rather than raw JSON.
"""

from __future__ import annotations

from dataclasses import dataclass


class DescriptionError(ValueError):
    """A $description document that cannot be parsed into the model.

    Raised rather than skipped: a checker that quietly validates nothing is worse
    than no checker.
    """


@dataclass(frozen=True)
class HomieProperty:
    id: str
    name: str
    datatype: str
    unit: str | None
    format: str | None
    settable: bool
    retained: bool


@dataclass(frozen=True)
class HomieNode:
    id: str
    name: str
    type: str | None
    properties: dict[str, HomieProperty]


@dataclass(frozen=True)
class HomieDevice:
    id: str
    name: str
    type: str | None
    nodes: dict[str, HomieNode]
    children: tuple[str, ...]
    root: str | None
    parent: str | None


@dataclass(frozen=True)
class HomieTree:
    devices: dict[str, HomieDevice]


def _as_dict(value: object, what: str) -> dict[str, object]:
    """Narrow an arbitrary JSON value to a string-keyed mapping.

    `json.loads` is typed `Any`; binding through `object` and narrowing here is what
    keeps `Any` out of the package under mypy --strict.
    """
    if not isinstance(value, dict):
        raise DescriptionError(f"{what}: expected an object, got {type(value).__name__}")
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise DescriptionError(f"{what}: non-string key {key!r}")
        narrowed[key] = item
    return narrowed


def _as_str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise DescriptionError(f"{what}: expected a string, got {type(value).__name__}")
    return value


def _opt_str(raw: dict[str, object], key: str, what: str) -> str | None:
    if key not in raw:
        return None
    return _as_str(raw[key], f"{what}.{key}")


def _parse_property(prop_id: str, value: object, what: str) -> HomieProperty:
    raw = _as_dict(value, what)
    if "datatype" not in raw:
        raise DescriptionError(f"{what}: missing datatype")
    # Homie omits these keys rather than publishing null, so absence is the default,
    # and `retained` absent means retained — note the inversion.
    return HomieProperty(
        id=prop_id,
        name=_opt_str(raw, "name", what) or prop_id,
        datatype=_as_str(raw["datatype"], f"{what}.datatype"),
        unit=_opt_str(raw, "unit", what),
        format=_opt_str(raw, "format", what),
        settable=raw.get("settable") is True,
        retained=raw.get("retained") is not False,
    )


def _parse_node(node_id: str, value: object, what: str) -> HomieNode:
    raw = _as_dict(value, what)
    properties_raw = _as_dict(raw.get("properties", {}), f"{what}.properties")
    properties = {
        prop_id: _parse_property(prop_id, prop, f"{what}.properties.{prop_id}")
        for prop_id, prop in properties_raw.items()
    }
    return HomieNode(
        id=node_id,
        name=_opt_str(raw, "name", what) or node_id,
        type=_opt_str(raw, "type", what),
        properties=properties,
    )


def parse_device(device_id: str, value: object) -> HomieDevice:
    """Parse one device's $description document."""
    what = f"device {device_id}"
    raw = _as_dict(value, what)
    nodes_raw = _as_dict(raw.get("nodes", {}), f"{what}.nodes")
    nodes = {
        node_id: _parse_node(node_id, node, f"{what}.nodes.{node_id}")
        for node_id, node in nodes_raw.items()
    }
    children_raw = raw.get("children", [])
    if not isinstance(children_raw, list):
        raise DescriptionError(f"{what}.children: expected a list")
    children = tuple(_as_str(child, f"{what}.children[]") for child in children_raw)
    return HomieDevice(
        id=device_id,
        name=_opt_str(raw, "name", what) or device_id,
        type=_opt_str(raw, "type", what),
        nodes=nodes,
        children=children,
        root=_opt_str(raw, "root", what),
        parent=_opt_str(raw, "parent", what),
    )


def build_tree(documents: dict[str, object]) -> HomieTree:
    """Parse a mapping of device id to $description document."""
    return HomieTree(devices={
        device_id: parse_device(device_id, raw) for device_id, raw in documents.items()
    })
```

- [ ] **Step 4: Run tests and type check**

Run: `uv run pytest tests/conformance/test_model.py -v && uv run mypy --strict src/panelbench/conformance/` Expected: 3 passed, mypy reports no issues.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/ tests/conformance/
git commit -m "feat: typed model for Homie 5 description documents"
```

---

### Task 2: Catalog loading and the abstract-unit set

**Files:**

- Create: `src/panelbench/conformance/catalogs.py`
- Test: `tests/conformance/test_catalogs.py`

**Interfaces:**

- Consumes: `DescriptionError` is _not_ used here; catalogs raise `CatalogError`.
- Produces: `CatalogProperty`, `Catalog`, `CatalogError`, `ABSTRACT_UNITS: frozenset[str]`, `load_catalogs(directory: Path) -> dict[str, Catalog]` keyed by
  capability type.

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_catalogs.py
from __future__ import annotations

from pathlib import Path

import pytest

from panelbench.conformance.catalogs import (
    ABSTRACT_UNITS,
    CatalogError,
    load_catalogs,
)

VENDORED = Path("src/panelbench/ebus_emitter/wire/catalogs")


def test_loads_vendored_catalogs_keyed_by_capability_type() -> None:
    catalogs = load_catalogs(VENDORED)
    assert "energy.ebus.capability.soc" in catalogs
    soc = catalogs["energy.ebus.capability.soc"]
    assert soc.version == "0.1"
    assert soc.properties["soc"].unit == "%"
    assert soc.properties["soe"].unit == "energy"


def test_rejects_a_directory_with_no_catalogs(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="no capability catalogs"):
        load_catalogs(tmp_path)


def test_every_vendored_unit_is_classified() -> None:
    """Guard against a re-vendor introducing a unit we silently mis-classify.

    ABSTRACT_UNITS is knowledge that does not travel with the vendored catalogs; this
    asserts our copy still covers them. `ebus_sdk.Unit` supplies the concrete set and is
    imported here, in a test, rather than in the package, which stays SDK-free.
    """
    import ebus_sdk

    unclassified: list[str] = []
    for catalog in load_catalogs(VENDORED).values():
        for prop in catalog.properties.values():
            if prop.unit is None or prop.unit in ABSTRACT_UNITS:
                continue
            try:
                ebus_sdk.Unit(prop.unit)
            except ValueError:
                unclassified.append(f"{catalog.capability}/{prop.id}: {prop.unit!r}")
    assert not unclassified, (
        "vendored catalog units that are neither a declared abstract token nor a unit "
        f"the SDK models: {unclassified}. Either upstream added an abstract token (extend "
        "ABSTRACT_UNITS) or added a concrete unit the SDK enum lacks (report upstream)."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_catalogs.py -v` Expected: FAIL with `ModuleNotFoundError: No module named 'panelbench.conformance.catalogs'`

- [ ] **Step 3: Write the implementation**

```python
# src/panelbench/conformance/catalogs.py
"""Vendored eBus capability catalogs, loaded as data.

Reads the JSON copies under ``ebus_emitter/wire/catalogs`` without importing anything
from the emitter: the checker must be able to validate a tree it did not build.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Units that name a *dimension* rather than a unit. A publisher MUST substitute a
# concrete unit before publishing; the token is never valid on the wire.
#
# This set is the one piece of catalog semantics that does NOT travel with the vendored
# JSON: upstream declares it in conventions/property-json.md and again in ABSTRACT_UNITS
# in the specification's tools/check-property-catalogs.py, neither of which a downstream
# copies. Until it becomes a vendorable artifact, we carry it here — and
# test_every_vendored_unit_is_classified fails on the next re-vendor if upstream adds one.
ABSTRACT_UNITS: frozenset[str] = frozenset({"energy"})


class CatalogError(ValueError):
    """A capability catalog that cannot be loaded."""


@dataclass(frozen=True)
class CatalogProperty:
    id: str
    datatype: str
    unit: str | None
    req: str
    format: str | None
    settable: bool


@dataclass(frozen=True)
class Catalog:
    capability: str
    version: str
    properties: dict[str, CatalogProperty]


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogError(f"{what}: expected an object, got {type(value).__name__}")
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise CatalogError(f"{what}: non-string key {key!r}")
        narrowed[key] = item
    return narrowed


def _as_str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise CatalogError(f"{what}: expected a string, got {type(value).__name__}")
    return value


def _opt_str(raw: dict[str, object], key: str, what: str) -> str | None:
    if key not in raw:
        return None
    return _as_str(raw[key], f"{what}.{key}")


def _parse_property(prop_id: str, value: object, what: str) -> CatalogProperty:
    raw = _as_dict(value, what)
    if "datatype" not in raw:
        raise CatalogError(f"{what}: missing datatype")
    return CatalogProperty(
        id=prop_id,
        datatype=_as_str(raw["datatype"], f"{what}.datatype"),
        unit=_opt_str(raw, "unit", what),
        req=_opt_str(raw, "req", what) or "MAY",
        format=_opt_str(raw, "format", what),
        settable=raw.get("settable") is True,
    )


def _load_one(path: Path) -> Catalog:
    parsed: object = json.loads(path.read_text())
    raw = _as_dict(parsed, str(path))
    capability = _opt_str(raw, "capability", str(path))
    if capability is None:
        raise CatalogError(f"{path}: no 'capability' field; not a capability catalog")
    properties_raw = _as_dict(raw.get("properties", {}), f"{path}.properties")
    properties = {
        prop_id: _parse_property(prop_id, prop, f"{path}:{prop_id}")
        for prop_id, prop in properties_raw.items()
    }
    return Catalog(
        capability=capability,
        version=_opt_str(raw, "version", str(path)) or "unknown",
        properties=properties,
    )


def load_catalogs(directory: Path) -> dict[str, Catalog]:
    """Load every capability catalog in *directory*, keyed by capability type."""
    catalogs: dict[str, Catalog] = {}
    for path in sorted(directory.glob("*.json")):
        catalog = _load_one(path)
        catalogs[catalog.capability] = catalog
    if not catalogs:
        raise CatalogError(f"no capability catalogs found in {directory}")
    return catalogs
```

- [ ] **Step 4: Run tests and type check**

Run: `uv run pytest tests/conformance/test_catalogs.py -v && uv run mypy --strict src/panelbench/conformance/` Expected: 3 passed, mypy clean.
`test_every_vendored_unit_is_classified` passing confirms `energy` is the only abstract token across all 15 catalogs.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/catalogs.py tests/conformance/test_catalogs.py
git commit -m "feat: load vendored capability catalogs for conformance checking"
```

---

### Task 3: Violations V1–V4

**Files:**

- Create: `src/panelbench/conformance/rules.py`
- Test: `tests/conformance/test_violations.py`

**Interfaces:**

- Consumes: `HomieTree`, `HomieDevice`, `HomieNode`, `HomieProperty` from `model`; `Catalog`, `CatalogProperty`, `ABSTRACT_UNITS` from `catalogs`.
- Produces: `Severity`, `Bucket`, `Finding`, `check_violations(tree: HomieTree, catalogs: dict[str, Catalog]) -> list[Finding]`.

Rules, and the authority for each — a reviewer should be able to check these against the design's "conformance floor" section:

| Rule | Check                                                                                | Authority                                                         |
| ---- | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| V1   | an `enum` or `color` property carries a `format`                                     | Homie 5 marks `format` required for exactly these two datatypes   |
| V2   | a published `unit` is never an abstract token                                        | `property-json.md`: "`energy` is never published on the wire"     |
| V3   | a property whose catalog entry carries an abstract token publishes a concrete `unit` | `property-json.md`: "A publisher MUST substitute a concrete unit" |
| V4   | every device named as a child has a description document                             | the standing self-description condition                           |

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_violations.py
from __future__ import annotations

from pathlib import Path

from panelbench.conformance.catalogs import load_catalogs
from panelbench.conformance.model import HomieTree, build_tree
from panelbench.conformance.rules import check_violations

CATALOGS = load_catalogs(Path("src/panelbench/ebus_emitter/wire/catalogs"))


def _tree(prop: dict[str, object], prop_id: str = "soe") -> HomieTree:
    return build_tree({"bess-1": {
        "name": "BESS", "type": "energy.ebus.device.bess", "children": [],
        "nodes": {"soc": {"name": "State", "type": "energy.ebus.capability.soc",
                          "properties": {prop_id: prop}}}}})


def test_v1_enum_without_format_is_a_violation() -> None:
    findings = check_violations(_tree({"name": "S", "datatype": "enum"}, "status"), CATALOGS)
    assert [f.rule for f in findings] == ["V1"]


def test_v2_abstract_token_on_the_wire_is_a_violation() -> None:
    findings = check_violations(
        _tree({"name": "SoE", "datatype": "float", "unit": "energy"}), CATALOGS)
    assert "V2" in [f.rule for f in findings]


def test_v3_missing_unit_where_catalog_is_abstract_is_a_violation() -> None:
    """The defect this whole tool exists for: not a wrong unit, an absent one."""
    findings = check_violations(_tree({"name": "SoE", "datatype": "float"}), CATALOGS)
    assert [f.rule for f in findings] == ["V3"]


def test_v3_passes_when_a_concrete_unit_is_substituted() -> None:
    findings = check_violations(
        _tree({"name": "SoE", "datatype": "float", "unit": "kWh"}), CATALOGS)
    assert findings == []


def test_v4_dangling_child_is_a_violation() -> None:
    tree = build_tree({"root-1": {"name": "R", "type": "t", "nodes": {},
                                  "children": ["missing-child"]}})
    findings = check_violations(tree, CATALOGS)
    assert [f.rule for f in findings] == ["V4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_violations.py -v` Expected: FAIL with `ModuleNotFoundError: No module named 'panelbench.conformance.rules'`

- [ ] **Step 3: Write the implementation**

```python
# src/panelbench/conformance/rules.py
"""Conformance rules over a parsed Homie tree.

Two kinds of output, and the split is the whole design:

* **Violations** break Homie 5 or one of eBus's few explicit MUSTs. They fail a build.
* **Observations** record legal divergence, extension and omission. They never fail.

The specification is deliberately permissive — publishing is opt-in, datatypes may be
widened, uncatalogued properties are allowed — so a checker that gates on catalog match
would fail conformant publishers. See docs/spec-conformance-design.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .catalogs import ABSTRACT_UNITS, Catalog
from .model import HomieDevice, HomieNode, HomieProperty, HomieTree

# Homie 5 marks `format` required for exactly these datatypes and optional for all others.
_FORMAT_REQUIRED_DATATYPES = frozenset({"enum", "color"})


class Severity(enum.Enum):
    VIOLATION = "violation"
    OBSERVATION = "observation"


class Bucket(enum.Enum):
    MATCH = "match"
    DIVERGENCE = "divergence"
    EXTENSION = "extension"
    OMISSION = "omission"
    VIOLATION = "violation"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    bucket: Bucket
    message: str
    device: str
    node: str | None = None
    property: str | None = None


def _violation(rule: str, message: str, device: str,
               node: str | None = None, prop: str | None = None) -> Finding:
    return Finding(rule=rule, severity=Severity.VIOLATION, bucket=Bucket.VIOLATION,
                   message=message, device=device, node=node, property=prop)


def _check_property(
    device: HomieDevice, node: HomieNode, prop: HomieProperty, catalog: Catalog | None
) -> list[Finding]:
    findings: list[Finding] = []

    if prop.datatype in _FORMAT_REQUIRED_DATATYPES and prop.format is None:
        findings.append(_violation(
            "V1", f"datatype '{prop.datatype}' requires a format in Homie 5, none published",
            device.id, node.id, prop.id))

    if prop.unit is not None and prop.unit in ABSTRACT_UNITS:
        findings.append(_violation(
            "V2", f"unit '{prop.unit}' is an abstract token and is never valid on the wire; "
                  "a publisher must substitute a concrete unit",
            device.id, node.id, prop.id))

    catalog_prop = catalog.properties.get(prop.id) if catalog is not None else None
    if (
        catalog_prop is not None
        and catalog_prop.unit is not None
        and catalog_prop.unit in ABSTRACT_UNITS
        and prop.unit is None
    ):
        findings.append(_violation(
            "V3", f"catalog unit '{catalog_prop.unit}' is an abstract token, so a concrete "
                  "unit must be substituted, but no unit was published",
            device.id, node.id, prop.id))

    return findings


def check_violations(tree: HomieTree, catalogs: dict[str, Catalog]) -> list[Finding]:
    """Every rule that fails a build. Empty means conformant."""
    findings: list[Finding] = []

    for device in tree.devices.values():
        for child_id in device.children:
            if child_id not in tree.devices:
                findings.append(_violation(
                    "V4", f"child device '{child_id}' is named but publishes no description",
                    device.id))
        for node in device.nodes.values():
            catalog = catalogs.get(node.type) if node.type is not None else None
            for prop in node.properties.values():
                findings.extend(_check_property(device, node, prop, catalog))

    return findings
```

- [ ] **Step 4: Run tests and type check**

Run: `uv run pytest tests/conformance/test_violations.py -v && uv run mypy --strict src/panelbench/conformance/` Expected: 5 passed, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/rules.py tests/conformance/test_violations.py
git commit -m "feat: conformance violations for abstract units and required formats"
```

---

### Task 4: Observations O1–O7 and O9

**Files:**

- Modify: `src/panelbench/conformance/rules.py`
- Test: `tests/conformance/test_observations.py`

**Interfaces:**

- Consumes: everything from Task 3.
- Produces: `check_observations(tree: HomieTree, catalogs: dict[str, Catalog]) -> list[Finding]`.

O8 needs device profiles and lands in Task 5. The numeric datatypes for O5 are the Homie set: `integer`, `float`.

| Rule | Check                                                                    | Bucket     |
| ---- | ------------------------------------------------------------------------ | ---------- |
| O1   | node type matches `energy.ebus.capability.*` but names no loaded catalog | Divergence |
| O2   | published datatype differs from the catalog's                            | Divergence |
| O3   | published unit differs from the catalog's concrete unit                  | Divergence |
| O4   | published settability differs from the catalog's                         | Divergence |
| O5   | unit present on a non-numeric datatype                                   | Divergence |
| O6   | published property absent from the catalog                               | Extension  |
| O7   | node type is not an `energy.ebus.capability.*` at all                    | Extension  |
| O9   | catalog property the device does not publish                             | Omission   |

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_observations.py
from __future__ import annotations

from pathlib import Path

from panelbench.conformance.catalogs import load_catalogs
from panelbench.conformance.model import HomieTree, build_tree
from panelbench.conformance.rules import Bucket, Finding, check_observations

CATALOGS = load_catalogs(Path("src/panelbench/ebus_emitter/wire/catalogs"))


def _tree(node_type: str, properties: dict[str, object]) -> HomieTree:
    return build_tree({"bess-1": {
        "name": "BESS", "type": "energy.ebus.device.bess", "children": [],
        "nodes": {"soc": {"name": "State", "type": node_type, "properties": properties}}}})


def _rules(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


def test_o1_unregistered_ebus_capability_type() -> None:
    tree = _tree("energy.ebus.capability.nonesuch", {})
    findings = check_observations(tree, CATALOGS)
    assert _rules(findings, "O1")


def test_o2_widened_datatype_is_a_divergence_not_a_violation() -> None:
    tree = _tree("energy.ebus.capability.soc",
                 {"soc": {"name": "SoC", "datatype": "string", "unit": "%"}})
    o2 = _rules(check_observations(tree, CATALOGS), "O2")
    assert o2 and o2[0].bucket is Bucket.DIVERGENCE


def test_o6_uncatalogued_property_is_an_extension() -> None:
    tree = _tree("energy.ebus.capability.soc",
                 {"span-extra": {"name": "Extra", "datatype": "float"}})
    o6 = _rules(check_observations(tree, CATALOGS), "O6")
    assert o6 and o6[0].bucket is Bucket.EXTENSION


def test_o7_non_ebus_node_type_is_an_extension() -> None:
    findings = check_observations(_tree("vendor.custom.thing", {}), CATALOGS)
    assert _rules(findings, "O7")


def test_o9_unpublished_catalog_property_is_an_omission() -> None:
    tree = _tree("energy.ebus.capability.soc",
                 {"soc": {"name": "SoC", "datatype": "float", "unit": "%"}})
    findings = check_observations(tree, CATALOGS)
    omitted = {f.property for f in _rules(findings, "O9")}
    assert {"soe", "total-energy-storage", "loadup-headroom"} <= omitted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_observations.py -v` Expected: FAIL with `ImportError: cannot import name 'check_observations'`

- [ ] **Step 3: Write the implementation**

Append to `rules.py`:

```python
_EBUS_CAPABILITY_PREFIX = "energy.ebus.capability."
_NUMERIC_DATATYPES = frozenset({"integer", "float"})


def _observation(rule: str, bucket: Bucket, message: str, device: str,
                 node: str | None = None, prop: str | None = None) -> Finding:
    return Finding(rule=rule, severity=Severity.OBSERVATION, bucket=bucket,
                   message=message, device=device, node=node, property=prop)


def _observe_property(
    device: HomieDevice, node: HomieNode, prop: HomieProperty, catalog: Catalog
) -> list[Finding]:
    findings: list[Finding] = []
    catalog_prop = catalog.properties.get(prop.id)

    if catalog_prop is None:
        findings.append(_observation(
            "O6", Bucket.EXTENSION,
            f"property '{prop.id}' is not in catalog {catalog.capability}; published as an "
            "extension, which the specification permits",
            device.id, node.id, prop.id))
        return findings

    if prop.datatype != catalog_prop.datatype:
        findings.append(_observation(
            "O2", Bucket.DIVERGENCE,
            f"datatype '{prop.datatype}' differs from catalog '{catalog_prop.datatype}'",
            device.id, node.id, prop.id))

    # Only a concrete catalog unit is comparable. An abstract token is handled by V2/V3.
    if (
        catalog_prop.unit is not None
        and catalog_prop.unit not in ABSTRACT_UNITS
        and prop.unit != catalog_prop.unit
    ):
        findings.append(_observation(
            "O3", Bucket.DIVERGENCE,
            f"unit {prop.unit!r} differs from catalog {catalog_prop.unit!r}",
            device.id, node.id, prop.id))

    if prop.settable != catalog_prop.settable:
        findings.append(_observation(
            "O4", Bucket.DIVERGENCE,
            f"settable={prop.settable} differs from catalog settable={catalog_prop.settable}",
            device.id, node.id, prop.id))

    if prop.unit is not None and prop.datatype not in _NUMERIC_DATATYPES:
        findings.append(_observation(
            "O5", Bucket.DIVERGENCE,
            f"unit {prop.unit!r} on non-numeric datatype '{prop.datatype}'",
            device.id, node.id, prop.id))

    return findings


def check_observations(tree: HomieTree, catalogs: dict[str, Catalog]) -> list[Finding]:
    """Legal divergence, extension and omission. Never fails a build."""
    findings: list[Finding] = []

    for device in tree.devices.values():
        for node in device.nodes.values():
            if node.type is None or not node.type.startswith(_EBUS_CAPABILITY_PREFIX):
                findings.append(_observation(
                    "O7", Bucket.EXTENSION,
                    f"node type {node.type!r} is not an eBus capability; published as an "
                    "extension, which the specification permits",
                    device.id, node.id))
                continue

            catalog = catalogs.get(node.type)
            if catalog is None:
                findings.append(_observation(
                    "O1", Bucket.DIVERGENCE,
                    f"node type '{node.type}' names no capability we vendored",
                    device.id, node.id))
                continue

            for prop in node.properties.values():
                findings.extend(_observe_property(device, node, prop, catalog))

            for catalog_prop_id in catalog.properties:
                if catalog_prop_id not in node.properties:
                    findings.append(_observation(
                        "O9", Bucket.OMISSION,
                        f"catalog property '{catalog_prop_id}' is not published; publishing "
                        "is opt-in, so this records coverage rather than a defect",
                        device.id, node.id, catalog_prop_id))

    return findings
```

- [ ] **Step 4: Run tests and type check**

Run: `uv run pytest tests/conformance/test_observations.py -v && uv run mypy --strict src/panelbench/conformance/` Expected: 5 passed, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/rules.py tests/conformance/test_observations.py
git commit -m "feat: classify divergence, extension and omission as observations"
```

---

### Task 5: Device profiles and O8

**Files:**

- Create: `src/panelbench/conformance/device_profiles.py`
- Modify: `src/panelbench/conformance/rules.py`
- Test: `tests/conformance/test_device_profiles.py`

**Interfaces:**

- Consumes: `Bucket`, `Finding`, `Severity`, `HomieTree` from earlier tasks.
- Produces: `DeviceProfile`, `ProfileError`, `load_device_profiles(directory: Path) -> dict[str, DeviceProfile]` keyed by device type;
  `check_profile_coverage(tree, profiles) -> list[Finding]`.

A device profile's `device_types` maps a device type to the capability nodes it composes. O8 reports a composed capability the device does not publish. It is an
**omission**, never a violation: profile `req` is "capability-level conformance guidance", default MAY.

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_device_profiles.py
from __future__ import annotations

import json
from pathlib import Path

from panelbench.conformance.device_profiles import load_device_profiles
from panelbench.conformance.model import build_tree
from panelbench.conformance.rules import Bucket, check_profile_coverage


def _profile_dir(tmp_path: Path) -> Path:
    (tmp_path / "bess.json").write_text(json.dumps({
        "device": "energy.ebus.device.bess",
        "device_types": {"energy.ebus.device.bess": {"role": "parent", "capabilities": {
            "info": {"catalog": "energy.ebus.capability.info", "req": "MUST"},
            "soc": {"catalog": "energy.ebus.capability.soc", "req": "MUST"}}}}}))
    return tmp_path


def test_loads_profiles_keyed_by_device_type(tmp_path: Path) -> None:
    profiles = load_device_profiles(_profile_dir(tmp_path))
    profile = profiles["energy.ebus.device.bess"]
    assert profile.capabilities == {"info": "energy.ebus.capability.info",
                                    "soc": "energy.ebus.capability.soc"}


def test_o8_reports_a_composed_capability_the_device_omits(tmp_path: Path) -> None:
    profiles = load_device_profiles(_profile_dir(tmp_path))
    tree = build_tree({"bess-1": {
        "name": "BESS", "type": "energy.ebus.device.bess", "children": [],
        "nodes": {"soc": {"name": "S", "type": "energy.ebus.capability.soc",
                          "properties": {}}}}})
    findings = check_profile_coverage(tree, profiles)
    assert [(f.rule, f.node, f.bucket) for f in findings] == [
        ("O8", "info", Bucket.OMISSION)]


def test_o8_silent_for_an_unprofiled_device_type(tmp_path: Path) -> None:
    profiles = load_device_profiles(_profile_dir(tmp_path))
    tree = build_tree({"x-1": {"name": "X", "type": "vendor.custom.device",
                               "children": [], "nodes": {}}})
    assert check_profile_coverage(tree, profiles) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_device_profiles.py -v` Expected: FAIL with
`ModuleNotFoundError: No module named 'panelbench.conformance.device_profiles'`

- [ ] **Step 3: Write the implementation**

```python
# src/panelbench/conformance/device_profiles.py
"""Vendored eBus device profiles: which capabilities a device type composes.

Distinct from the *conformance report* this package produces. A device profile is
upstream's data; the report is our output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ProfileError(ValueError):
    """A device profile that cannot be loaded."""


@dataclass(frozen=True)
class DeviceProfile:
    device_type: str
    role: str | None
    capabilities: dict[str, str]
    """Node id to the capability type it implements."""


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProfileError(f"{what}: expected an object, got {type(value).__name__}")
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ProfileError(f"{what}: non-string key {key!r}")
        narrowed[key] = item
    return narrowed


def _opt_str(raw: dict[str, object], key: str, what: str) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise ProfileError(f"{what}.{key}: expected a string")
    return value


def load_device_profiles(directory: Path) -> dict[str, DeviceProfile]:
    """Load every device profile in *directory*, keyed by device type.

    Sub-directories are not searched: the SPAN overlay lives under ``profiles/span`` and
    is applied by the emitter, not by the checker, which compares against base catalogs.
    """
    profiles: dict[str, DeviceProfile] = {}
    for path in sorted(directory.glob("*.json")):
        parsed: object = json.loads(path.read_text())
        raw = _as_dict(parsed, str(path))
        device_types = _as_dict(raw.get("device_types", {}), f"{path}.device_types")
        for device_type, entry_raw in device_types.items():
            entry = _as_dict(entry_raw, f"{path}.device_types.{device_type}")
            capabilities_raw = _as_dict(
                entry.get("capabilities", {}), f"{path}.{device_type}.capabilities")
            capabilities: dict[str, str] = {}
            for node_id, ref_raw in capabilities_raw.items():
                ref = _as_dict(ref_raw, f"{path}.{device_type}.capabilities.{node_id}")
                catalog = _opt_str(ref, "catalog", f"{path}.{device_type}.{node_id}")
                if catalog is None:
                    raise ProfileError(
                        f"{path}: {device_type}.{node_id} has no 'catalog' reference")
                capabilities[node_id] = catalog
            profiles[device_type] = DeviceProfile(
                device_type=device_type,
                role=_opt_str(entry, "role", f"{path}.{device_type}"),
                capabilities=capabilities,
            )
    return profiles
```

Append to `rules.py`:

```python
def check_profile_coverage(
    tree: HomieTree, profiles: dict[str, DeviceProfile]
) -> list[Finding]:
    """O8: capabilities a device type's profile composes but the device does not publish.

    An omission, never a violation. A profile's ``req`` is capability-level *guidance*
    with a MAY default, and publishing is opt-in, so an absent capability is a fact about
    this device rather than a defect.
    """
    findings: list[Finding] = []
    for device in tree.devices.values():
        if device.type is None:
            continue
        profile = profiles.get(device.type)
        if profile is None:
            continue
        for node_id, capability in profile.capabilities.items():
            if node_id not in device.nodes:
                findings.append(_observation(
                    "O8", Bucket.OMISSION,
                    f"profile for '{device.type}' composes '{capability}' as node "
                    f"'{node_id}', which this device does not publish",
                    device.id, node_id))
    return findings
```

Add the import at the top of `rules.py`:

```python
from .device_profiles import DeviceProfile
```

- [ ] **Step 4: Run tests and type check**

Run: `uv run pytest tests/conformance/ -v && uv run mypy --strict src/panelbench/conformance/` Expected: all pass, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/ tests/conformance/test_device_profiles.py
git commit -m "feat: report capabilities a device profile composes but omits"
```

---

### Task 6: The conformance report

**Files:**

- Create: `src/panelbench/conformance/report.py`
- Test: `tests/conformance/test_report.py`

**Interfaces:**

- Consumes: `Finding`, `Bucket`, `Severity` from `rules`.
- Produces: `ConformanceReport`, `build_report(findings: list[Finding], match_count: int) -> ConformanceReport`, `render_text(report) -> str`,
  `render_json(report) -> str`.

The report is the deliverable, not the violations. Counts come first, then violations in full, then observations grouped by rule. Matches are counted rather
than listed — they are the uninteresting majority.

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_report.py
from __future__ import annotations

import json

from panelbench.conformance.report import build_report, render_json, render_text
from panelbench.conformance.rules import Bucket, Finding, Severity

VIOLATION = Finding(rule="V3", severity=Severity.VIOLATION, bucket=Bucket.VIOLATION,
                    message="no unit published", device="bess-1", node="soc", property="soe")
EXTENSION = Finding(rule="O6", severity=Severity.OBSERVATION, bucket=Bucket.EXTENSION,
                    message="not in catalog", device="bess-1", node="soc", property="extra")


def test_report_counts_by_bucket() -> None:
    report = build_report([VIOLATION, EXTENSION], match_count=12)
    assert report.counts == {"match": 12, "divergence": 0, "extension": 1,
                             "omission": 0, "violation": 1}
    assert report.conformant is False


def test_report_is_conformant_with_observations_only() -> None:
    assert build_report([EXTENSION], match_count=3).conformant is True


def test_render_json_round_trips() -> None:
    parsed = json.loads(render_json(build_report([VIOLATION], match_count=1)))
    assert parsed["conformant"] is False
    assert parsed["violations"][0]["rule"] == "V3"
    assert parsed["violations"][0]["property"] == "soe"


def test_render_text_leads_with_the_verdict() -> None:
    text = render_text(build_report([VIOLATION], match_count=1))
    assert "1 violation" in text
    assert "bess-1/soc/soe" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_report.py -v` Expected: FAIL with `ModuleNotFoundError: No module named 'panelbench.conformance.report'`

- [ ] **Step 3: Write the implementation**

```python
# src/panelbench/conformance/report.py
"""The conformance report: what this publisher emits, relative to the specification.

The report is the deliverable. Violations fail a build, but the classification —
match, divergence, extension, omission — is what a consumer author actually needs,
because it states the contract this producer offers and is derived from the wire
rather than from prose that drifts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .rules import Bucket, Finding, Severity


@dataclass(frozen=True)
class ConformanceReport:
    counts: dict[str, int]
    violations: tuple[Finding, ...]
    observations: tuple[Finding, ...]

    @property
    def conformant(self) -> bool:
        return not self.violations


def build_report(findings: list[Finding], match_count: int) -> ConformanceReport:
    counts = {bucket.value: 0 for bucket in Bucket}
    counts[Bucket.MATCH.value] = match_count
    for finding in findings:
        counts[finding.bucket.value] += 1
    return ConformanceReport(
        counts=counts,
        violations=tuple(f for f in findings if f.severity is Severity.VIOLATION),
        observations=tuple(f for f in findings if f.severity is Severity.OBSERVATION),
    )


def _path(finding: Finding) -> str:
    parts = [finding.device]
    if finding.node is not None:
        parts.append(finding.node)
    if finding.property is not None:
        parts.append(finding.property)
    return "/".join(parts)


def _as_dict(finding: Finding) -> dict[str, str | None]:
    return {"rule": finding.rule, "bucket": finding.bucket.value, "path": _path(finding),
            "device": finding.device, "node": finding.node, "property": finding.property,
            "message": finding.message}


def render_json(report: ConformanceReport) -> str:
    """Machine-readable form. A consumer can assert against this directly."""
    return json.dumps({
        "conformant": report.conformant,
        "counts": report.counts,
        "violations": [_as_dict(f) for f in report.violations],
        "observations": [_as_dict(f) for f in report.observations],
    }, indent=2, sort_keys=True)


def render_text(report: ConformanceReport, verbose: bool = False) -> str:
    lines: list[str] = []
    counts = report.counts
    lines.append(
        f"conformance: {counts['match']} match, {counts['divergence']} divergence, "
        f"{counts['extension']} extension, {counts['omission']} omission")

    if report.violations:
        lines.append("")
        lines.append(f"{len(report.violations)} violation(s):")
        for finding in report.violations:
            lines.append(f"  {finding.rule}  {_path(finding)}: {finding.message}")
    else:
        lines.append("  no violations")

    # Omissions are the bulk of any real tree — a full BESS omits many optional
    # properties — so they stay behind --verbose rather than burying the violations.
    shown = [f for f in report.observations
             if verbose or f.bucket is not Bucket.OMISSION]
    if shown:
        lines.append("")
        lines.append("observations (never fatal):")
        for finding in shown:
            lines.append(f"  {finding.rule}  {_path(finding)}: {finding.message}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests and type check**

Run: `uv run pytest tests/conformance/test_report.py -v && uv run mypy --strict src/panelbench/conformance/` Expected: 4 passed, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/report.py tests/conformance/test_report.py
git commit -m "feat: render the conformance report as text and JSON"
```

---

### Task 7: Feeds

**Files:**

- Create: `src/panelbench/conformance/feeds.py`
- Test: `tests/conformance/test_feeds.py`

**Interfaces:**

- Consumes: `build_tree` from `model`.
- Produces: `Described` protocol, `from_devices(devices: Iterable[Described]) -> dict[str, object]`, `from_capture(path: Path) -> dict[str, object]`,
  `CaptureError`.

Both feeds return the same thing — a mapping of device id to raw `$description` document — so every rule runs unchanged over either. `from_devices` is
duck-typed via a `Protocol` so the package stays free of `ebus_sdk`.

Capture file format: one JSON object mapping device id to its description document. A capture is produced by `mosquitto_sub` and reshaped by the CLI in Task 8;
committing that shape here keeps the parser trivial and diffable.

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_feeds.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from panelbench.conformance.feeds import CaptureError, from_capture, from_devices


class _FakeDevice:
    def __init__(self, device_id: str) -> None:
        self._id = device_id

    def id(self) -> str:
        return self._id

    def description(self) -> dict[str, object]:
        return {"name": self._id, "type": "t", "nodes": {}, "children": []}


def test_from_devices_keys_by_device_id() -> None:
    documents = from_devices([_FakeDevice("a"), _FakeDevice("b")])
    assert sorted(documents) == ["a", "b"]


def test_from_capture_reads_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({"d1": {"name": "d", "type": "t",
                                       "nodes": {}, "children": []}}))
    assert list(from_capture(path)) == ["d1"]


def test_from_capture_rejects_a_non_object(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(CaptureError, match="mapping of device id"):
        from_capture(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_feeds.py -v` Expected: FAIL with `ModuleNotFoundError: No module named 'panelbench.conformance.feeds'`

- [ ] **Step 3: Write the implementation**

```python
# src/panelbench/conformance/feeds.py
"""Ways of obtaining $description documents.

Both feeds return the same shape — device id to raw description document — so the rules
run unchanged over an in-process tree or a capture off a live broker. That is the point:
the in-process feed catches defects at authoring time, the capture feed proves the wire
matches what we composed.

No live-broker mode. It would drag credentials and a running broker into CI, be
non-deterministic, and weld the checker to one transport.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


class CaptureError(ValueError):
    """A capture file that cannot be read."""


class Described(Protocol):
    """Anything that can report a Homie id and description.

    A Protocol rather than an import: it is structurally satisfied by ``ebus_sdk.Device``
    without this package depending on the SDK.
    """

    def id(self) -> str: ...

    def description(self) -> dict[str, object]: ...


def from_devices(devices: Iterable[Described]) -> dict[str, object]:
    """Description documents straight from a built device tree, no broker involved."""
    return {device.id(): device.description() for device in devices}


def from_capture(path: Path) -> dict[str, object]:
    """Description documents from a capture file written by scripts/check-conformance.py."""
    parsed: object = json.loads(path.read_text())
    if not isinstance(parsed, dict):
        raise CaptureError(
            f"{path}: expected a mapping of device id to description document, "
            f"got {type(parsed).__name__}")
    documents: dict[str, object] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            raise CaptureError(f"{path}: non-string device id {key!r}")
        documents[key] = value
    return documents
```

- [ ] **Step 4: Run tests and type check**

Run: `uv run pytest tests/conformance/test_feeds.py -v && uv run mypy --strict src/panelbench/conformance/` Expected: 3 passed, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/feeds.py tests/conformance/test_feeds.py
git commit -m "feat: in-process and capture-file feeds for conformance checking"
```

---

### Task 8: Public API, import boundary, and the CLI

**Files:**

- Modify: `src/panelbench/conformance/__init__.py`
- Create: `scripts/check-conformance.py`
- Test: `tests/conformance/test_boundary.py`

**Interfaces:**

- Consumes: everything from Tasks 1–7.
- Produces: `check(tree, catalogs, profiles) -> ConformanceReport` exported from the package.

The boundary test is the load-bearing one. It is what keeps the core reusable — and what would let it be offered upstream, where no publisher-side checker
exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_boundary.py
"""The conformance package must not depend on this simulator, or on any transport.

The rules describe *any* eBus publisher's output. A single convenience import from the
emitter would silently make them describe only ours.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/panelbench/conformance")
FORBIDDEN_ROOTS = {"panelbench", "ebus_sdk", "aiomqtt", "paho"}


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import (level > 0) stays inside the package.
            if node.level == 0 and node.module is not None:
                roots.add(node.module.split(".")[0])
    return roots


def test_package_imports_nothing_forbidden() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        bad = _imported_roots(path) & FORBIDDEN_ROOTS
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        f"conformance package must stay independent of the simulator and of any "
        f"transport, but found: {offenders}")
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/conformance/test_boundary.py -v` Expected: PASS. This test guards a property the earlier tasks already established; if it fails, an
earlier task introduced a forbidden import and that is the bug.

- [ ] **Step 3: Write the public API and the CLI**

```python
# src/panelbench/conformance/__init__.py
"""Conformance checking of a published Homie 5 tree against the vendored eBus catalogs.

Independent of this simulator by design: see tests/conformance/test_boundary.py.
"""

from __future__ import annotations

from .catalogs import Catalog, CatalogError, load_catalogs
from .device_profiles import DeviceProfile, ProfileError, load_device_profiles
from .feeds import CaptureError, from_capture, from_devices
from .model import DescriptionError, HomieTree, build_tree
from .report import ConformanceReport, build_report, render_json, render_text
from .rules import (
    Bucket,
    Finding,
    Severity,
    check_observations,
    check_profile_coverage,
    check_violations,
)

__all__ = [
    "Bucket", "Catalog", "CatalogError", "CaptureError", "ConformanceReport",
    "DescriptionError", "DeviceProfile", "Finding", "HomieTree", "ProfileError",
    "Severity", "build_report", "build_tree", "check", "check_observations",
    "check_profile_coverage", "check_violations", "from_capture", "from_devices",
    "load_catalogs", "load_device_profiles", "render_json", "render_text",
]


def check(
    tree: HomieTree,
    catalogs: dict[str, Catalog],
    profiles: dict[str, DeviceProfile],
) -> ConformanceReport:
    """Run every rule and aggregate into a report."""
    findings = [
        *check_violations(tree, catalogs),
        *check_observations(tree, catalogs),
        *check_profile_coverage(tree, profiles),
    ]
    matches = 0
    for device in tree.devices.values():
        for node in device.nodes.values():
            catalog = catalogs.get(node.type) if node.type is not None else None
            if catalog is None:
                continue
            flagged = {
                f.property for f in findings
                if f.device == device.id and f.node == node.id and f.property is not None
            }
            matches += sum(
                1 for prop_id in node.properties
                if prop_id in catalog.properties and prop_id not in flagged
            )
    return build_report(findings, match_count=matches)
```

```python
#!/usr/bin/env python3
# scripts/check-conformance.py
"""Check what the simulator publishes against the catalogs it vendored.

Companion to check-spec-provenance.py, and the two compose:

    check-spec-provenance.py    specification bytes -> our vendored catalogs
    check-conformance.py        vendored catalogs   -> what we actually publish

Provenance proves we copied the right bytes. This proves we understood them.

Usage:
    scripts/check-conformance.py --capture tree.json          # check a capture
    scripts/check-conformance.py --capture tree.json --json   # machine-readable report

Produce a capture from a running simulator with:
    mosquitto_sub -h localhost -p 18883 --cafile .local/certs/ca.crt \\
        -u span -P sim-password -t 'ebus/5/+/$description' -v -W 5 \\
        | scripts/check-conformance.py --from-stdin > tree.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from panelbench.conformance import (  # noqa: E402
    build_tree,
    check,
    from_capture,
    load_catalogs,
    load_device_profiles,
    render_json,
    render_text,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOGS = REPO_ROOT / "src/panelbench/ebus_emitter/wire/catalogs"
PROFILES = REPO_ROOT / "src/panelbench/ebus_emitter/wire/profiles"


def _reshape_stdin() -> int:
    """Turn `mosquitto_sub -v` output into the capture shape, on stdout.

    Each line is `<topic> <payload>`; we keep the device id from the topic and the
    payload as the document.
    """
    documents: dict[str, object] = {}
    for line in sys.stdin:
        topic, _, payload = line.partition(" ")
        if not payload.strip() or not topic.endswith("/$description"):
            continue
        device_id = topic.split("/")[2]
        documents[device_id] = json.loads(payload)
    json.dump(documents, sys.stdout, indent=2, sort_keys=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capture", type=pathlib.Path,
                        help="capture file of $description documents")
    parser.add_argument("--from-stdin", action="store_true",
                        help="reshape mosquitto_sub -v output into a capture, on stdout")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--verbose", action="store_true", help="include omissions")
    args = parser.parse_args()

    if args.from_stdin:
        return _reshape_stdin()
    if args.capture is None:
        parser.error("one of --capture or --from-stdin is required")

    report = check(
        build_tree(from_capture(args.capture)),
        load_catalogs(CATALOGS),
        load_device_profiles(PROFILES),
    )
    print(render_json(report) if args.json else render_text(report, verbose=args.verbose))
    return 0 if report.conformant else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verify end to end**

```bash
chmod +x scripts/check-conformance.py
uv run pytest tests/conformance/ -v
uv run mypy --strict src/panelbench/conformance/
```

Expected: all tests pass, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/panelbench/conformance/__init__.py scripts/check-conformance.py tests/conformance/test_boundary.py
git commit -m "feat: conformance CLI and package API with an enforced import boundary"
```

---

### Task 9: Golden fixture, golden report, and documentation

**Files:**

- Create: `tests/conformance/fixtures/golden_tree.json`
- Create: `tests/conformance/fixtures/golden_report.json`
- Create: `tests/conformance/test_golden.py`
- Modify: `DEVELOPER.md` (the Spec Conformance section added earlier)

**Interfaces:**

- Consumes: `check`, `build_tree`, `from_capture`, `render_json` from the package.
- Produces: no new API. This task makes the tool load-bearing.

The golden **report** is the stronger of the two assertions: it fails when our published surface changes in any way, including legally. A legal change should be
a reviewed edit to the expected report, not something that slips through because no rule covered it.

`Device.description()` includes `version`, a timestamp, so the captured fixture is not byte-stable across runs. The model discards `version`, so the report is
stable — which is exactly why the report, not the capture, is what gets asserted.

- [ ] **Step 1: Capture a real tree**

Start the parent/child simulator, capture, and reshape:

```bash
simpc &
sleep 20
mosquitto_sub -h localhost -p 18883 \
  --cafile /Users/bflood/projects/span/simulator/.local/certs/ca.crt \
  -u span -P sim-password -t 'ebus/5/+/$description' -v -W 5 \
  | uv run scripts/check-conformance.py --from-stdin \
  > tests/conformance/fixtures/golden_tree.json
simstop
```

Verify the capture is non-empty and contains more than one device:

```bash
uv run python -c "import json,pathlib; d=json.loads(pathlib.Path('tests/conformance/fixtures/golden_tree.json').read_text()); print(len(d), 'devices')"
```

Expected: more than one device. If it prints `0`, the simulator had not finished publishing — increase the sleep and retry.

- [ ] **Step 2: Write the failing test**

```python
# tests/conformance/test_golden.py
"""The captured tree is the contract this simulator offers a consumer.

test_golden_report_is_unchanged fails on ANY change to the published surface, including
a perfectly legal one. That is deliberate: a legal change should be a reviewed edit to
the expected report, not something that slips through because no rule covered it.
"""

from __future__ import annotations

import json
from pathlib import Path

from panelbench.conformance import (
    ConformanceReport,
    build_tree,
    check,
    from_capture,
    load_catalogs,
    load_device_profiles,
    render_json,
)

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGS = Path("src/panelbench/ebus_emitter/wire/catalogs")
PROFILES = Path("src/panelbench/ebus_emitter/wire/profiles")


def _report() -> ConformanceReport:
    tree = build_tree(from_capture(FIXTURES / "golden_tree.json"))
    return check(tree, load_catalogs(CATALOGS), load_device_profiles(PROFILES))


def test_golden_tree_has_no_violations() -> None:
    report = _report()
    assert report.conformant, [f"{f.rule} {f.device}/{f.node}/{f.property}: {f.message}"
                               for f in report.violations]


def test_golden_report_is_unchanged() -> None:
    expected = json.loads((FIXTURES / "golden_report.json").read_text())
    actual = json.loads(render_json(_report()))
    assert actual == expected, (
        "the published surface changed. If the change is intended, regenerate with:\n"
        "  uv run scripts/check-conformance.py "
        "--capture tests/conformance/fixtures/golden_tree.json --json "
        "> tests/conformance/fixtures/golden_report.json")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/conformance/test_golden.py -v` Expected: `test_golden_report_is_unchanged` FAILS with `FileNotFoundError: golden_report.json`.
`test_golden_tree_has_no_violations` should PASS — if it does not, the listed violations are real defects in the emitter and must be fixed before continuing.

- [ ] **Step 4: Generate the expected report and re-run**

```bash
uv run scripts/check-conformance.py \
  --capture tests/conformance/fixtures/golden_tree.json --json \
  > tests/conformance/fixtures/golden_report.json
uv run pytest tests/conformance/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Wire the command into DEVELOPER.md**

In the **Spec Conformance** section, replace the status `designed` with `in CI` in the table row for `scripts/check-conformance.py`, and add to the existing
code block:

````markdown
```bash
# Check a captured tree against the vendored catalogs
uv run scripts/check-conformance.py --capture tests/conformance/fixtures/golden_tree.json

# Recapture from a running simulator (see docs/spec-conformance-design.md)
mosquitto_sub -h localhost -p 18883 --cafile .local/certs/ca.crt \
  -u span -P sim-password -t 'ebus/5/+/$description' -v -W 5 \
  | uv run scripts/check-conformance.py --from-stdin > tests/conformance/fixtures/golden_tree.json
```
````

- [ ] **Step 6: Commit**

```bash
git add tests/conformance/fixtures/ tests/conformance/test_golden.py DEVELOPER.md
git commit -m "test: assert the published tree's conformance report against a golden capture"
```

---

## Deferred, deliberately

Recorded so a later reader knows these were decided rather than missed:

- **`from_devices` is built but not yet wired into the emitter's test suite.** The feed and its test exist; adding a test that builds a real panel tree and
  checks it in-process belongs with the emitter's own tests, not here, and needs the emitter's fixtures.
- **Property values.** Range and enum-membership conformance needs the value topics, not just `$description`.
- **Temporal rules.** Monotonicity of cumulative registers needs a capture window rather than a snapshot.
- **Release-to-release diffing.** Two reports diffed give a complete change enumeration between releases; see the design document's "Future work" section. The
  JSON report shape in Task 6 is what makes it possible, which is why it is JSON rather than text-only.
