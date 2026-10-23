"""Actual owner ControlServer + real H2 backend + guarded native Node consumer.

An unmerged provider may be supplied as an explicitly staged immutable source
fixture. Neither provider nor client path is a production lookup mechanism.
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import config, handoff_prompt_receipt, handoff_trace, installer, tracking
from agent_procutil.owned_process import run_caller_owned


def load_provider(name, monkeypatch):
    root = os.environ.get("RECEIPT_PROVIDER_SOURCE")
    if root:
        path = Path(root) / (name + ".py")
        module_name = f"agent_worktrees.{name}" if name == "handoff_prompt_receipt_control" else f"agent_worktrees._fixture_{name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module_name, module)
        spec.loader.exec_module(module)
        return module
    return __import__(f"agent_worktrees.{name}", fromlist=[name])


@pytest.fixture
def resident(sample_record, tmp_tracking_dir, tmp_path, monkeypatch):
    adapter = load_provider("handoff_prompt_receipt_control", monkeypatch)
    control = load_provider("status_monitor_cutover", monkeypatch)
    node = shutil.which("node")
    assert node, "native receipt fixture requires Node"
    assert Path(os.environ["RECEIPT_CLIENT_ROOT"]).is_dir(), "explicit client payload fixture required"
    root = tmp_path / "resident"
    root.mkdir()
    sid = "receipt-subject"
    sample_record.creation_nonce = "receipt-incarnation"
    sample_record.sessions = [
        tracking.SessionEntry(sid, "2026-01-01"),
        tracking.SessionEntry("different-head", "2026-01-02"),
    ]
    sample_record.head_session = "different-head"
    record_path = tmp_tracking_dir / f"{sample_record.worktree_id}.yaml"
    tracking.save_record(sample_record, record_path)
    monkeypatch.setattr(config, "install_dir", lambda: root)
    monkeypatch.setattr(config, "tracking_dir", lambda project=None: tmp_tracking_dir)
    monkeypatch.setattr(installer, "read_projects_registry", lambda **_kw: {
        "projects": {sample_record.repo: {}},
    })
    monkeypatch.setattr(control, "load_or_create_control_token",
                        lambda *a, **kw: pytest.fail("credential creation on observation"))
    identity = {
        "installationId": "fixture-installation", "sourceFingerprint": "fixture-source",
        "marketplaceId": "fixture-marketplace", "pluginId": "agent-worktrees",
    }
    owner = {
        **identity, "routeGeneration": 1, "pid": os.getpid(),
        "processStartTime": "fixture-start", "runtimeGeneration": "fixture-runtime",
    }
    counts = {"helpers": 0, "fsync": 0, "record": 0, "query": 0, "maxActive": 0}
    fsync = os.fsync
    hold_sync, release_sync = threading.Event(), threading.Event()
    state = SimpleNamespace(drop_reply=False, delay_sync=False, hold_sync=hold_sync,
                            release_sync=release_sync, counts=counts)
    popen = subprocess.Popen

    def guarded_popen(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            counts["helpers"] += 1
            raise AssertionError("resident path started a helper")
        return popen(*args, **kwargs)  # Only the test harness launches its Node peer.

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    monkeypatch.setenv("PATH", "")

    def controlled_sync(fd):
        counts["fsync"] += 1
        if state.delay_sync:
            hold_sync.set()
            assert release_sync.wait(8), "fixture fsync was not released"
        fsync(fd)

    monkeypatch.setattr(handoff_prompt_receipt.os, "fsync", controlled_sync)
    runtime = None

    def backend(*args, **kwargs):
        counts["maxActive"] = max(counts["maxActive"], runtime.active_count())
        counts["record" if kwargs["record"] else "query"] += 1
        return handoff_prompt_receipt.resident_prompt_receipt(*args, **kwargs)

    runtime = adapter.ReceiptControl(lambda: dict(owner), backend=backend)
    current = threading.local()
    original_handler = control._ControlHandler

    class CapturedHandler(original_handler):
        def setup(self):
            super().setup()
            current.socket = self.request

    monkeypatch.setattr(control, "_ControlHandler", CapturedHandler)
    requests = []

    def handle(action, payload):
        requests.append(action)
        if action == "health":
            return runtime.health()
        result = runtime.handle(payload)
        if state.drop_reply and payload.get("operation") == "record":
            current.socket.shutdown(2)
            current.socket.close()
        return result

    server = control.ControlServer(handle, token="fixture-token")
    session_dir = tmp_path / "session-state" / sid
    guidance_dir = session_dir / "instructions" / "agent-worktrees"
    guidance_dir.mkdir(parents=True)
    route_path, token_path = root / "active.json", root / "control.token"
    route_path.write_text(json.dumps({"active": {
        "bind": "127.0.0.1", "port": server.port, "generation": 1,
        "pid": owner["pid"], "process_start_time": owner["processStartTime"],
    }}), encoding="utf-8")
    token_path.write_text("fixture-token", encoding="utf-8")
    subject = {
        "sessionId": sid, "project": sample_record.repo, "worktreeId": sample_record.worktree_id,
        "creationNonce": sample_record.creation_nonce,
    }
    (session_dir / "worktree-binding.json").write_text(json.dumps(subject), encoding="utf-8")
    catalog = {
        "schema": "copilot-extensions.session-command-catalog", "version": 1, "plugin": "agent-worktrees",
        "binding": {
            "schema": "copilot-extensions.command-installation-binding", "version": 1,
            "status": "selected",
            "source": {"fingerprint": identity["sourceFingerprint"],
                       "marketplaceId": identity["marketplaceId"], "pluginId": "agent-worktrees"},
            "installation": {**identity, "runtimeRoot": str(root)},
        },
        "residentControl": {
            **identity, "version": 1, "state": "candidate", "runtimeRoot": str(root),
            "activeRoutePath": str(route_path), "tokenPath": str(token_path),
        },
    }
    (guidance_dir / "session-guidance.instructions.md").write_text(
        "```json\n" + json.dumps(catalog) + "\n```\n", encoding="utf-8",
    )
    driver = Path(__file__).with_name("receipt_node_driver.mjs")

    def observe(*, pending=None, locator="task:native-fixture", concurrent=0):
        request_file = tmp_path / "node-request.json"
        request_file.write_text(json.dumps({
            "sessionDir": str(session_dir), "sessionId": sid,
            "locator": locator, "pending": pending or {}, "concurrent": concurrent,
        }), encoding="utf-8")
        result = run_caller_owned(
            [node, str(driver), str(request_file)], timeout=6,
            max_stdout_bytes=16384, max_stderr_bytes=16384,
        )
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["helperCalls"] == 0
        assert value["elapsedMs"] < 2000
        return value

    state.observe = observe
    state.runtime = runtime
    state.record = sample_record
    state.record_path = record_path
    state.subject = subject
    state.root = root
    state.requests = requests
    state.route_path = route_path
    state.token_path = token_path
    state.session_dir = session_dir
    threads_before = {thread.ident for thread in threading.enumerate()}
    server.start()
    try:
        yield state
    finally:
        release_sync.set()
        runtime.close_admission()
        server.close()
        runtime.wait_idle()
        assert runtime.active_count() == 0
        assert not server.thread.is_alive()
        end = time.monotonic() + 2
        while any(thread.ident not in threads_before for thread in threading.enumerate()):
            assert time.monotonic() < end, "owned fixture handler did not settle"
            time.sleep(0.01)
        assert counts["helpers"] == 0


@pytest.mark.parametrize("cycle", [1, 2])
def test_actual_control_real_backend_node_two_cycles(resident, cycle):
    first = resident.observe()
    assert first["status"] == "logged"
    assert resident.observe(pending=first["pending"])["status"] == "logged"
    rows = handoff_trace.read_trace(resident.record.repo, resident.record.worktree_id)
    assert len(rows) == 1 and rows[0]["creation_nonce"] == resident.record.creation_nonce
    assert resident.counts["fsync"] == 1
    assert resident.counts["record"] == 2
    assert resident.counts["helpers"] == 0
    assert resident.counts["maxActive"] <= 4


def test_lost_reply_after_fsync_reconciles_exact_identity_without_second_write(resident):
    resident.drop_reply = True
    first = resident.observe()
    assert first["status"] == "unknown"
    assert resident.counts["fsync"] == 1
    resident.drop_reply = False
    replay = resident.observe(pending=first["pending"])
    assert replay["status"] == "logged"
    assert resident.counts["record"] == 1 and resident.counts["query"] == 1
    assert resident.counts["fsync"] == 1


def test_concurrent_node_observers_record_one_exact_receipt(resident):
    batch = resident.observe(concurrent=2)
    assert any(result["status"] == "logged" for result in batch["results"])
    for result in batch["results"]:
        if result["status"] != "logged":
            assert resident.observe(pending=result["pending"])["status"] == "logged"
    assert resident.counts["fsync"] == 1
    assert len(handoff_trace.read_trace(resident.record.repo, resident.record.worktree_id)) == 1
    assert resident.counts["maxActive"] <= 4


def test_exact_pending_successor_is_supported_without_head_promotion(resident):
    resident.record.sessions = [tracking.SessionEntry("different-head", "2026-01-02")]
    resident.record.handoffs = [tracking.SessionHandoff(
        ordinal=1, token="fixture-handoff", predecessor="different-head",
        opened_at="2026-01-02", candidate=resident.subject["sessionId"],
    )]
    tracking.save_record(resident.record, resident.record_path)
    assert resident.observe()["status"] == "logged"
    assert tracking.load_record(resident.record_path).head_session == "different-head"


def test_disconnected_client_drain_keeps_real_fsync_owned_until_settlement(resident):
    resident.delay_sync = True
    first = resident.observe()
    assert resident.hold_sync.is_set() and first["status"] == "unknown"
    assert resident.runtime.active_count() == 1
    resident.runtime.close_admission()
    assert resident.observe(pending=first["pending"])["status"] == "unknown"
    assert resident.runtime.active_count() == 1
    resident.release_sync.set()
    end = time.monotonic() + 3
    while resident.runtime.active_count():
        assert time.monotonic() < end
        time.sleep(0.01)
    resident.runtime.open_admission()  # Fixture owner, never a client action.
    assert resident.observe(pending=first["pending"])["status"] == "logged"
    assert resident.counts["record"] == 1 and resident.counts["fsync"] == 1


def test_actual_control_refuses_wrong_nonce_and_missing_association(resident):
    binding = resident.session_dir / "worktree-binding.json"
    binding.write_text(json.dumps({**resident.subject, "creationNonce": "foreign"}), encoding="utf-8")
    assert resident.observe()["status"] in {"unavailable", "unknown"}
    binding.write_text(json.dumps({**resident.subject, "sessionId": "foreign"}), encoding="utf-8")
    assert resident.observe()["status"] == "unavailable"
    assert resident.counts["fsync"] == 0
