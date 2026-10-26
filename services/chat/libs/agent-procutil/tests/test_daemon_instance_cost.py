"""Bounded local cost sample; timings are evidence, not host-speed gates."""

import json
import os
from pathlib import Path
import sys
import time

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, cancel_instance_reservation, reconcile_instances,
    reserve_instance, spawn_finite_worker,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="actual launch measurement requires canonical containment",
)


def test_bounded_launch_and_zero_one_entry_reconciliation_cost(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    repetitions = 20
    start = time.perf_counter()
    for _ in range(repetitions):
        assert reconcile_instances(registry_root=registry) == []
    empty = (time.perf_counter() - start) * 1000 / repetitions
    spec = DaemonInstanceSpec(
        "cost-fixture", tmp_path, Path(__file__).resolve(), registry_root=registry,
    )
    receipt = reserve_instance(spec)
    identity_calls = []
    original = store.identity_state

    def identity(*args):
        identity_calls.append(args)
        return original(*args)

    with monkeypatch.context() as patch:
        patch.setattr(store, "identity_state", identity)
        start = time.perf_counter()
        for _ in range(repetitions):
            assert reconcile_instances(registry_root=registry) == []
        one = (time.perf_counter() - start) * 1000 / repetitions
    assert identity_calls == [], "production scan must not enumerate/query processes"
    cancel_instance_reservation(receipt, creation_refused=True)
    launches = []
    for _ in range(3):
        start = time.perf_counter()
        process = spawn_finite_worker(
            [sys.executable, "-c", "pass"], instance=spec, breakaway=False,
            cwd=tmp_path, log_path=tmp_path / "cost.log",
        )
        launches.append((time.perf_counter() - start) * 1000)
        try:
            process.wait(timeout=8)
            assert reconcile_instances(registry_root=registry)[0].status == "settled"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    print(json.dumps({
        "sample": "local-contained", "reconcile_repetitions": repetitions,
        "empty_reconcile_ms_per_call": round(empty, 3),
        "one_production_reconcile_ms_per_call": round(one, 3),
        "production_process_identity_queries": len(identity_calls),
        "finite_launch_samples_ms": [round(value, 3) for value in launches],
    }, sort_keys=True))
