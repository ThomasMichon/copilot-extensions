"""Opt-in real legacy-v1 RPC, canonical code holds and native exit proof."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager, ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import direct_python, spawn_sync_in_kill_on_close_job
from zdd import diagnostics, routing

from agent_worktrees import historical_daemons as owner
from agent_worktrees import locks

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32" and not sys.platform.startswith("linux"),
                       reason="native retirement requires Windows or Linux"),
    pytest.mark.skipif(os.environ.get("AGENT_WORKTREES_NATIVE_RECOVERY") != "1",
                       reason="opt in with AGENT_WORKTREES_NATIVE_RECOVERY=1"),
]


@contextmanager
def peer(root, slot, mode):
    helper = Path(__file__).with_name("native_historical_daemon_provider.py")
    python, additions = direct_python(sys.executable)
    with (root / (slot + ".log")).open("w", encoding="utf-8") as log:
        arguments = [python, str(helper), str(root), slot, mode]
        options = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                       env={**os.environ, **additions}, cwd=str(root / "versions" / slot / ".venv"))
        if sys.platform == "win32":
            proc, job = spawn_sync_in_kill_on_close_job(
                arguments, require_job=True, allow_breakaway=False,
                creationflags=subprocess.CREATE_NO_WINDOW, **options,
            )
            assert job is not None
        else:
            proc, job = subprocess.Popen(arguments, **options), None
        try:
            ready = root / (slot + "-ready.json")
            deadline = time.monotonic() + 10
            while not ready.exists():
                assert proc.poll() is None, (root / (slot + ".log")).read_text()
                if time.monotonic() >= deadline:
                    pytest.fail("disposable legacy peer did not become ready")
                time.sleep(.01)
            data = json.loads(ready.read_text())
            assert data["pid"] == proc.pid and data["start"] == locks.process_start_time(proc.pid)
            yield proc, routing.Endpoint("127.0.0.1", data["port"], proc.pid,
                                         process_start_time=data["start"])
        finally:
            if job is not None:
                job.terminate_and_wait(5)
                assert job.handle is None
            elif proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            proc.wait(timeout=5)
            assert proc.returncode is not None


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / "libs/versioned-runtime/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("historical_native_runtime", source)
    helper = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, helper)
    spec.loader.exec_module(helper)
    root = tmp_path / "runtime"
    for slot in ("old", "new"):
        (root / "versions" / slot / ".venv").mkdir(parents=True)
        helper.mark_complete(root, slot, payload_hash=slot)
    helper.activate(root, "new", link_free=True)
    (root / "status-monitor-control.token").write_text("historical-native-fixture", encoding="utf-8")
    monkeypatch.setattr(owner.retention, "_runtime", lambda: helper)
    return root, helper


@pytest.mark.parametrize("mode", ["graceful", "needs-force", "busy"])
def test_native_selected_old_peer_preserves_current_and_settles_exact_code_hold(runtime, monkeypatch, mode):
    root, helper = runtime
    directory = root / "status-monitor-routing"
    with ExitStack() as stack:
        old, old_route = stack.enter_context(peer(root, "old", mode))
        current, current_route = stack.enter_context(peer(root, "new", "graceful"))
        old_route = routing.publish_active(directory, bind=old_route.bind, port=old_route.port, pid=old.pid,
                               process_start_time=old_route.process_start_time, version="old")
        routing.publish_active_with_previous_guarded(
            directory, bind=current_route.bind, port=current_route.port, pid=current.pid,
            process_start_time=current_route.process_start_time, version="new",
            expected_active=old_route, demote_existing=True,
        )
        candidates = [diagnostics.DaemonCandidate(pid=endpoint.pid, start_time=endpoint.process_start_time)
                      for endpoint in (old_route, current_route)]
        ctx = SimpleNamespace(config_dir=directory, group_launchers=False, is_launcher_image=None,
                              list_candidates=lambda: [item for item in candidates if locks.pid_alive(item.pid)])
        monkeypatch.setattr(owner.daemon_health, "_context", lambda: ctx)
        selected = next(item for item in owner.inspect(root) if item["status"] == "candidate")
        assert not helper._leases.read_runtime_references(root)
        if mode == "busy":
            result = owner.apply(root, selected)
            assert result["status"] == "pending" and old.poll() is None
            assert not (root / "old-shutdown-requested").exists()
            assert helper._leases.read_runtime_references(root)
            (root / "release-work").touch()
        result = owner.apply(root, selected)
        assert result["status"] == "applied", result
        old.wait(timeout=5)
        assert old.returncode is not None and current.poll() is None
        assert not helper._leases.read_runtime_references(root)
        assert routing.read_active_endpoint(directory, verify_listener=False).pid == current.pid
        assert owner._client(root, current_route).health()["status"] == "ready"
