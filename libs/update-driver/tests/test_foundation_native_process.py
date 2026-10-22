"""Actual native custody may admit pending work, but its death never replays installation."""

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

import agent_procutil as pu
from update_driver import (
    InterruptedError, RequestSpec, TrustedRunner, drain, enqueue,
    foundation_status, kernel_boot_identity, status, wait,
)
from update_driver import foundation_state as fs
from update_driver._directory import create_queue_directory

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual native Windows foundation custody")


def test_native_bootstrap_death_fences_pending_python_work_without_replay(area):
    root = area / "state"
    create_queue_directory(root)
    operation = fs.FoundationOperation(
        "a" * 32, "b" * 64, kernel_boot_identity(), "running", time.time(),
    )
    (root / "fixture-operation.json").write_text(
        json.dumps({"schema": fs.SCHEMA, "version": fs.VERSION, **asdict(operation)}),
        encoding="utf-8",
    )
    base = Path(__file__).resolve().parents[2]
    helper = base / "single-instance-lease" / "src" / "single_instance_lease" / "operation.ps1"
    fixture = Path(__file__).with_name("fixture_foundation_native_owner.ps1")
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    with (root / "native.log").open("w") as log:
        parent, job = pu.spawn_sync_in_kill_on_close_job(
            [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
             str(root), str(helper)], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            **pu.no_window_kwargs(),
        )
        try:
            deadline = time.monotonic() + 15
            while not (root / "native-ready").exists():
                assert parent.poll() is None, (root / "native.log").read_text()
                if time.monotonic() >= deadline:
                    pytest.fail("native foundation owner did not reach its custody gate")
                time.sleep(.02)
            queued = enqueue(
                RequestSpec("native-foundation-fixture"), root=root,
                runner=TrustedRunner((sys.executable, "-c", "pass"), str(root)),
            )
            assert queued.state == "pending" and parent.poll() is None
            assert foundation_status(root=root).state == "running"
            parent.kill()
            parent.wait(timeout=5)
            with pytest.raises(InterruptedError, match="foundation"):
                wait(queued.id, root=root, timeout=1)
            interrupted = foundation_status(root=root)
            assert interrupted.state == "interrupted" and interrupted.exit_code is None
            assert interrupted.operation_id == operation.operation_id
            assert interrupted.error == "owner_lost_custody"
            executed = []
            with pytest.raises(InterruptedError, match="foundation"):
                drain(root=root, executor=lambda tx: executed.append(tx.request.id) or 0)
            assert executed == []
            assert status(queued.id, root=root).state == "pending"
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=5)
            if job is not None:
                job.close()
