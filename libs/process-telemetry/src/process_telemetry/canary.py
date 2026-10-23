"""Explicit synthetic OTLP canary; never invoked by a hook or auto-installer."""

from __future__ import annotations

import argparse
import json

from . import SinkSlot
from .measurement import begin
from .otlp import LocalOtlpSink


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    records = []
    slot = SinkSlot()
    slot.set(records.append)
    measurement = begin(slot, "telemetry-canary", "ipc")
    measurement.finish(spawn_count=0)
    try:
        sink = LocalOtlpSink(args.endpoint, timeout=args.timeout)
        try:
            sink.export_batch(records)
        finally:
            sink.close()
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps({
        "schema": "agent.telemetry-canary.v1", "result": "exported",
        "trace_id": measurement.context.trace_id, "span_id": measurement.context.span_id,
        "persistence": "not-verified",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
