"""Native source regression: retained Windows process objects are not live."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.test_source_caller_job_native import _NativeProcess

pytestmark = pytest.mark.skipif(os.name != "nt", reason="retained Windows native process identity")


def test_exited_coordinator_with_retained_native_birth_is_confirmed_dead(tmp_path):
    from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_test_lifecycle, spawn_instance
    from agent_procutil._daemon_instance_identity import birth_identity, stop_identity
    from agent_dispatch import source_start
    from zdd.diagnostics import process_start_time

    cleanup, pin, process = [], None, None
    proof = {}
    try:
        with daemon_test_lifecycle(
            owner="dispatch-exited-route-native", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            process = spawn_instance(
                [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
                instance=DaemonInstanceSpec(
                    "dispatch-exited-route-control", tmp_path, Path(__file__),
                    purpose="unattended-test", lifecycle="finite-worker",
                ),
                cwd=tmp_path, env={**os.environ, **overlay},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            birth = birth_identity(process.pid)
            token = process_start_time(process.pid)
            assert token
            pin = _NativeProcess(process.pid, birth)
            endpoint = SimpleNamespace(pid=process.pid, process_start_time=token)
            assert source_start._route_owner_state(endpoint) == "matching"
            assert stop_identity(process.pid, birth)
            assert not pin.running()
            assert process_start_time(process.pid) == token
            proof["native_handle_signalled_while_birth_still_queryable"] = True
            proof["observed_state"] = source_start._route_owner_state(endpoint)
            assert proof["observed_state"] == "dead"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if pin is not None:
            pin.close()
        proof["cleanup"] = [asdict(item) for item in cleanup]
        evidence = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
        if evidence:
            (Path(evidence) / "retained-process-birth-proof.json").write_text(
                json.dumps(proof, indent=2), encoding="utf-8",
            )
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)
