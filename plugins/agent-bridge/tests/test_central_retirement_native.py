"""Native central-only exit with a contained, independently surviving child."""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil import _daemon_instance_identity, process_retirement, spawn_sync_in_kill_on_close_job
from zdd import diagnostics, process_exit, routing

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.skipif(
        os.name != "nt" and not sys.platform.startswith("linux"),
        reason="native process retirement supports Windows and Linux",
    ),
]


def test_native_central_retirement_preserves_child_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import service_process_cli
    from agent_bridge.central_retirement import retire_predecessor

    receipt = tmp_path / "processes.json"
    script = (
        "import json,os,pathlib,subprocess,sys;"
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import os,sys;print(os.getpid(),flush=True);sys.stdin.buffer.read()'],"
        "stdin=sys.stdin,stdout=subprocess.PIPE,text=True);"
        "child_pid=int(child.stdout.readline());child.stdout.close();"
        "receipt=pathlib.Path(sys.argv[1]);pending=receipt.with_suffix('.pending');"
        "pending.write_text(json.dumps({'root':os.getpid(),'child':child_pid}));"
        "pending.replace(receipt);sys.stdin.buffer.read()"
    )
    process, job = spawn_sync_in_kill_on_close_job(
        [sys.executable, "-c", script, str(receipt)],
        require_job=os.name == "nt", allow_breakaway=False,
        stdin=subprocess.PIPE,
    )
    child = None
    try:
        deadline = time.monotonic() + 5
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        identities = json.loads(receipt.read_text())
        root_pid, child_pid = identities["root"], identities["child"]
        birth = _daemon_instance_identity.birth_identity(child_pid)
        child = process_retirement.PinnedProcess(child_pid, birth)
        old = routing.publish_active(
            tmp_path, bind="127.0.0.1", port=41001, pid=root_pid, version="fixture-old",
            process_start_time=diagnostics.process_start_time(root_pid),
        )
        current, _ = routing.publish_active_with_previous(
            tmp_path, bind="127.0.0.1", port=41002, pid=os.getpid(), version="fixture-new",
            process_start_time=diagnostics.process_start_time(os.getpid()), demote_existing=True,
        )
        monkeypatch.setattr(service_process_cli, "_pid_is_agent_bridge", lambda pid, **_: pid == root_pid)

        def shutdown() -> None:
            raise OSError("synthetic predecessor does not settle")

        client = SimpleNamespace(
            health=lambda: {
                "status": "ok", "service": "agent-bridge", "ready": True,
                "slot": {"pid": current.pid, "role": "active", "active": current.to_dict()},
            },
            shutdown=shutdown,
        )
        outcome = retire_predecessor(
            Path(tmp_path), old, current, make_client=lambda _: client, graceful_timeout=.01,
        )
        assert outcome == {"exit_confirmed": True, "forced": True}
        process.wait(timeout=5)
        assert old.pid is not None and old.process_start_time is not None
        assert process_exit.observe_original_exit(old.pid, old.process_start_time)
        assert not child.exited()
        assert _daemon_instance_identity.birth_identity(child_pid) == birth
        table = routing.read_table(tmp_path)
        assert table is not None and table["active"] == current.to_dict()
    finally:
        try:
            if child is not None:
                try:
                    child.terminate()
                finally:
                    child.close()
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            finally:
                if job is not None:
                    job.terminate_and_wait(5)
