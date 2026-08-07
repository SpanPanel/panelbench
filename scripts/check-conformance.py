#!/usr/bin/env python3
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

from panelbench.conformance import (
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
    # Trailing newline so a regenerated capture matches what the end-of-file-fixer
    # pre-commit hook would write, and regeneration is therefore idempotent.
    json.dump(documents, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--capture", type=pathlib.Path, help="capture file of $description documents"
    )
    parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="reshape mosquitto_sub -v output into a capture, on stdout",
    )
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
