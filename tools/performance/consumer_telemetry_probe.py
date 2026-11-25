"""Finite source-only measurements; no installed runtime or external receiver."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any


def measure(action: Callable[[], Any], count: int) -> dict[str, Any]:
    """Include timer and dispatch overhead; do not subtract a synthetic baseline."""
    samples = []
    cpu_start = time.process_time_ns()
    thread_start = time.thread_time_ns()
    wall_start = time.perf_counter_ns()
    for _ in range(count):
        start = time.perf_counter_ns()
        action()
        samples.append(time.perf_counter_ns() - start)
    elapsed = time.perf_counter_ns() - wall_start
    thread_cpu = time.thread_time_ns() - thread_start
    process_cpu = time.process_time_ns() - cpu_start
    ordered = sorted(samples)
    return {
        "operations": count, "wall_ns": elapsed,
        "caller_thread_cpu_ns": thread_cpu, "process_cpu_ns": process_cpu,
        "operation_wall_ns": {
            name: ordered[min(count - 1, math.ceil(count * fraction) - 1)]
            for name, fraction in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1))
        },
        "first_operation_wall_ns": samples[0],
        "operation_wall_samples_ns": samples,
        "quantile_method": "nearest-rank",
        "tail_qualification": "diagnostic-only; one finite trial",
    }


RECEIVER = r'''
import json, os, sys, time
from pathlib import Path
mode = sys.argv[1]
finite = len(sys.argv) > 2 and sys.argv[2] == "--expected-file"
if finite:
    target = Path("expected-records.json")
    deadline = time.monotonic() + 3
    while not target.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("fixture record count unavailable")
        time.sleep(.005)
    expected = json.loads(target.read_text(encoding="utf-8"))["records"]
else:
    expected = int(sys.argv[2]) if len(sys.argv) > 2 else None
if mode == "failed":
    raise SystemExit(7)
if mode == "nonreading":
    time.sleep(30)
cpu = time.process_time_ns()
wall = time.perf_counter_ns()
count = 0
with Path("readback.jsonl").open("wb") as output:
    for line in sys.stdin.buffer:
        if mode == "bridge-only":
            schema = json.loads(line)["source_schema"]
            if schema != "bridge.lifecycle.v1":
                with Path("schema-rejection.json").open("w", encoding="utf-8") as witness:
                    json.dump({"observed_source_schema": schema, "rejected": True}, witness)
                    witness.flush()
                    os.fsync(witness.fileno())
                raise SystemExit(8)
        if mode == "slow":
            time.sleep(.002)
        output.write(line)
        output.flush()
        if mode == "fsync-failed":
            raise OSError("fixture fsync refusal after readable write")
        os.fsync(output.fileno())
        count += 1
        if expected is not None and count == expected:
            witness = Path("settled.pending")
            witness.write_text(json.dumps({"records": count, "fsync_per_record": True}), encoding="utf-8")
            os.replace(witness, "settled.json")
            if finite:
                break
cost = Path("receiver-cost.pending")
cost.write_text(json.dumps({
    "records": count, "cpu_ns": time.process_time_ns() - cpu,
    "wall_ns": time.perf_counter_ns() - wall,
    "fsync_per_record": True
}), encoding="utf-8")
os.replace(cost, "receiver-cost.json")
'''
