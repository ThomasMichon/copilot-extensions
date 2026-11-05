"""Normal writer startup through an actual private, leased resident process."""

from __future__ import annotations

import argparse
import json
import sys
import time

import pytest
from agent_procutil.daemon_instances import daemon_test_lifecycle
from zdd import routing

from agent_worktrees import claims_cli, locks, tracking, tracking_write
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import status_monitor_reap_stale as interpreters
from agent_worktrees import status_monitor_runtime as monitor
from agent_worktrees.owner_observation_transport import HISTORY_ACTION, READ_ACTION
from _native_receipt_fixtures import environment, selection, setup

pytestmark = [
    pytest.mark.guard,
    pytest.mark.timeout(60),
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX resident process qualification"),
]


@pytest.mark.parametrize("maintenance", [False, True])
def test_normal_claim_wakes_private_resident_and_captures_transaction(
    tmp_path, monkeypatch, capfd, maintenance,
):
    owner, path, home = setup(tmp_path / "source", monkeypatch)
    owner.shutdown()
    runtime = home / ".agent-worktrees"
    runtime.mkdir(exist_ok=True)
    for key, value in environment(home).items():
        monkeypatch.setenv(key, value)
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    monkeypatch.setattr(interpreters, "current_runtime_python", lambda: sys.executable)
    if maintenance:
        (runtime / "maintenance").touch()
    handles = []
    spawn = control.spawn_monitor

    def capture_spawn(*args, **kwargs):
        process = spawn(*args, **kwargs)
        handles.append(process)
        return process

    monkeypatch.setattr(control, "spawn_monitor", capture_spawn)
    monkeypatch.setattr(tracking_write, "run_direct", lambda *args, **kwargs: pytest.fail("direct fallback"))
    args = argparse.Namespace(json=True, release_worktree="native-one", note="", claim_owner_ref=None)
    before = path.read_bytes()
    try:
        with daemon_test_lifecycle(
            owner="resident-recovery-process-test", registry_root=tmp_path / "instances",
        ) as overlay:
            for key, value in overlay.items():
                monkeypatch.setenv(key, value)
            assert locks.read_lock(monitor._monitor_lock_path()) is None
            result = claims_cli._claims_add(args, "codespace", "fixture-child")
            output = json.loads(capfd.readouterr().out)
            if maintenance:
                assert result == 1 and "maintenance" in output["error"]
                assert handles == [] and path.read_bytes() == before
                return
            assert result == 0, output
            assert len(handles) == 1
            process = handles[0]
            assert process.poll() is None
            lock = locks.read_lock(monitor._monitor_lock_path())
            assert lock["pid"] == process.pid
            assert lock["start_time"] == locks.process_start_time(process.pid)
            endpoint = routing.read_active_endpoint(control.routing_dir())
            assert endpoint.pid == process.pid
            client = control.ControlClient(endpoint.base_url, config_dir=control.routing_dir())
            assert client.health()
            snapshot = client._request(READ_ACTION, selection())
            deadline = time.monotonic() + 5
            while snapshot.get("error", {}).get("code") == "unready":
                assert process.poll() is None
                assert time.monotonic() < deadline, snapshot
                time.sleep(.02)
                snapshot = client._request(READ_ACTION, selection())
            assert "source_generation" in snapshot, snapshot
            request = selection()
            request.pop("after_cursor")
            request.update(
                source_generation=snapshot["source_generation"], before_cursor=None, limit=64,
            )
            history = client._request(HISTORY_ACTION, request)
            assert any(
                row["kind"] == "claim-added" and row["evidence_kind"] == "persisted-mutation"
                for row in history["entries"]
            )
            assert [claim.ref for claim in tracking.load_record(path).resources] == ["fixture-child"]
            assert claims_cli._claims_add(args, "codespace", "second-fixture-child") == 0
            capfd.readouterr()
            assert len(handles) == 1 and process.poll() is None
            client.shutdown()
    finally:
        for process in handles:
            process.wait(timeout=10)
            assert process.poll() is not None
            assert not process.instance_receipt.record_path.exists()
