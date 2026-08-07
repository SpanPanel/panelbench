#!/usr/bin/env python3
"""Recapture the wire fixture — descriptions, `$state`, and every property value.

A thin CLI over `panelbench.conformance.wire_capture`, which is where the work
lives so a test can check the committed fixture against what the emitter
currently emits.

`check-conformance.py --from-stdin` captures `$description` documents only,
because conformance is a question about declarations. This captures the whole
retained surface, because a consumer cannot be exercised by declarations alone.

**Values are not reproducible.** The config carries `noise_factor`, so power and
current differ every run, and the clock advances. Nothing should assert byte
equality against this file — assert structure and let the values be
representative. That is why it is a separate artifact from `golden_tree.json`,
whose report *is* compared exactly.

    uv run scripts/capture-wire.py                       # default 40-space panel
    uv run scripts/capture-wire.py --config configs/default_MAIN_16.yaml
    uv run scripts/capture-wire.py -o /tmp/wire.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from panelbench.emitter_adapter.wire_capture import capture

_REPO = pathlib.Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _REPO / "configs" / "default_MAIN_40.yaml"
_DEFAULT_OUTPUT = _REPO / "tests" / "conformance" / "fixtures" / "golden_wire.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=pathlib.Path, default=_DEFAULT_CONFIG)
    parser.add_argument("-o", "--output", type=pathlib.Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.config.exists():
        print(f"no such config: {args.config}", file=sys.stderr)
        return 1

    captured = asyncio.run(capture(args.config))
    if not captured:
        print("captured nothing; did the emitter start?", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    described = sum(1 for device in captured.values() if "$description" in device)
    values = sum(1 for device in captured.values() for key in device if not key.startswith("$"))
    print(
        f"{args.output}: {len(captured)} devices, {described} described, {values} property values"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
