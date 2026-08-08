"""Where the catalogs and profiles the producer actually published from live.

Conformance asks whether what this simulator publishes is legal. Answering that
against a *copy* of the rules answers a subtly different question — whether it
would have been legal under rules it did not use. So these resolve to the data
inside the installed ``ebus-panel-sim`` wheel: the same bytes the emitter
composed the tree from, by construction rather than by a test.

That leaves provenance needing a separate answer, because ``.ebus-spec.json`` is
this repository's claim about which specification commit it is synced to, and a
claim about a dependency's files would move on every pin bump. So ``spec/catalogs``
stays vendored and byte-checked against the specification, and
``test_catalogs.py`` ties the two together: spec == vendored == wheel. Without
that last link, conformance would measure against data nothing had vouched for.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def _package_dir(*parts: str) -> Path:
    """A real filesystem path inside the installed emitter package.

    ``files()`` rather than ``__file__`` so this keeps working if the package is
    ever loaded from somewhere other than a plain directory; the cast to ``Path``
    is safe for the directory-backed installs this project uses, and a zipimport
    would fail loudly here rather than silently reading nothing.
    """
    resource = files("ebus_panel_sim")
    for part in parts:
        resource = resource / part
    return Path(str(resource))


def emitter_catalogs() -> Path:
    """The capability catalogs the emitter composed the published tree from."""
    return _package_dir("wire", "catalogs")


def emitter_profiles() -> Path:
    """The device profiles, including the SPAN overlay, as shipped."""
    return _package_dir("wire", "profiles")
