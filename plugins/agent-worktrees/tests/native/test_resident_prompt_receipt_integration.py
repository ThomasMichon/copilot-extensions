"""Actual owner ControlServer + real H2 backend + guarded native Node consumer.

A separately owned provider may be supplied as an explicitly staged immutable source
fixture. Neither provider nor client path is a production lookup mechanism.
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import config, handoff_prompt_receipt, handoff_trace, installer, tracking
from agent_procutil.owned_process import run_caller_owned
from receipt_budget_fixture import receipt_budget_factor, validate_receipt_fixture_budgets


def load_provider(name, monkeypatch):
    root = os.environ.get("RECEIPT_PROVIDER_SOURCE")
    if root:
        path = Path(root) / (name + ".py")
        canonical = name in {"handoff_prompt_receipt_control", "handoff_diagnostics", "worktree_trust"}
        module_name = f"agent_worktrees.{name}" if canonical else f"agent_worktrees._fixture_{name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module_name, module)
        spec.loader.exec_module(module)
        if canonical:
            monkeypatch.setattr(sys.modules["agent_worktrees"], name, module, raising=False)
        return module
    return __import__(f"agent_worktrees.{name}", fromlist=[name])


@pytest.fixture
def resident(sample_record, tmp_tracking_dir, tmp_path, monkeypatch):
    factor = receipt_budget_factor()
    provider_root = os.environ.get("RECEIPT_PROVIDER_SOURCE") or (
        Path(__file__).parents[2] / "src" / "agent_worktrees"
    )
    budgets = validate_receipt_fixture_budgets(
        os.environ["RECEIPT_CLIENT_ROOT"], provider_root, factor,
    )
    for name, nominal in (
        ("MAX_LOCK_SECONDS", 0.1), ("MAX_SCAN_SECONDS", 0.2),
        ("MAX_PRE_EFFECT_SECONDS", 1.0),
    ):
        monkeypatch.setattr(handoff_prompt_receipt, name, nominal * factor)
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
                            release_sync=release_sync, counts=counts, cancel_path=None,
                            hold_backend=False, release_backend=threading.Event(),
                            oversized_reply=False)
    state.factor = factor
    popen = subprocess.Popen
    driver = Path(__file__).with_name("receipt_node_driver.mjs")

    def guarded_popen(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if (
            threading.current_thread() is not threading.main_thread()
            or not isinstance(command, (list, tuple))
            or list(command[:2]) != [node, str(driver)]
        ):
            counts["helpers"] += 1
            raise AssertionError("resident path started a helper")
        return popen(*args, **kwargs)  # Only the test harness launches its Node peer.

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    monkeypatch.setenv("PATH", "")

    def controlled_sync(fd):
        counts["fsync"] += 1
        if state.delay_sync:
            hold_sync.set()
            if state.cancel_path is not None:
                state.cancel_path.write_text("admitted", encoding="utf-8")
            assert release_sync.wait(8 * factor), "fixture fsync was not released"
        fsync(fd)

    monkeypatch.setattr(handoff_prompt_receipt.os, "fsync", controlled_sync)
    runtime = None

    def backend(*args, **kwargs):
        counts["maxActive"] = max(counts["maxActive"], runtime.active_count())
        counts["record" if kwargs["record"] else "query"] += 1
        if state.hold_backend:
            assert state.release_backend.wait(8 * factor), "fixture backend was not released"
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
        if state.oversized_reply:
            result["padding"] = "x" * 8192
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
    def observe(*, pending=None, locator="task:native-fixture", concurrent=0,
                cancel_before=False, cancel_on_fsync=False, distinct=False, callback=False):
        request_file = tmp_path / "node-request.json"
        state.cancel_path = tmp_path / "cancel-after-admission" if cancel_on_fsync else None
        if state.cancel_path is not None:
            state.cancel_path.write_text("", encoding="utf-8")
        request_file.write_text(json.dumps({
            "sessionDir": str(session_dir), "sessionId": sid,
            "locator": locator, "pending": pending or {}, "concurrent": concurrent,
            "distinct": distinct,
            "callback": callback,
            "budgetFactor": factor,
            "cancelBefore": cancel_before,
            **({"cancelFile": str(state.cancel_path)} if state.cancel_path is not None else {}),
        }), encoding="utf-8")
        result = run_caller_owned(
            [node, str(driver), str(request_file)], timeout=6 * factor,
            max_stdout_bytes=16384, max_stderr_bytes=16384,
        )
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        print(json.dumps({
            "observation": value["status"], "reason": value.get("reason"),
            "elapsedMs": value["elapsedMs"], "helperCalls": value["helperCalls"],
            "socketCalls": value["socketCalls"], "callbackMs": value.get("callbackMs"),
            "active": runtime.active_count(), "counts": dict(counts),
            **budgets, "nominalMet": value["nominalMet"], "scaledMet": value["scaledMet"],
            "callbackNominalMet": value["callbackNominalMet"],
            "callbackScaledMet": value["callbackScaledMet"],
            "metadataMode": value["metadataMode"],
        }), flush=True)
        assert value["helperCalls"] == 0
        assert value["budgetFactor"] == factor
        assert value["elapsedMs"] < 2000 * factor
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
    state.owner = owner
    state.port = server.port
    threads_before = {thread.ident for thread in threading.enumerate()}
    server.start()
    try:
        yield state
    finally:
        release_sync.set()
        state.release_backend.set()
        runtime.close_admission()
        server.close()
        runtime.wait_idle()
        assert runtime.active_count() == 0
        assert not server.thread.is_alive()
        end = time.monotonic() + 2 * factor
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
    assert resident.counts["fsync"] == 2
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
    assert resident.counts["fsync"] == 2


def test_concurrent_node_observers_record_one_exact_receipt(resident):
    batch = resident.observe(concurrent=2)
    assert any(result["status"] == "logged" for result in batch["results"])
    for result in batch["results"]:
        if result["status"] != "logged":
            assert resident.observe(pending=result["pending"])["status"] == "logged"
    assert resident.counts["fsync"] == 2
    assert len(handoff_trace.read_trace(resident.record.repo, resident.record.worktree_id)) == 1
    assert resident.counts["maxActive"] <= 4


def test_exact_pending_successor_is_supported_without_head_promotion(resident):
    resident.record.sessions = [tracking.SessionEntry("different-head", "2026-01-02")]
    resident.record.handoffs = [tracking.SessionHandoff(
        ordinal=1, token="fixture-handoff", predecessor="different-head",
        state="pending", opened_at="2026-01-02", candidate=resident.subject["sessionId"],
    )]
    tracking.save_record(resident.record, resident.record_path)
    observed = resident.observe(callback=True)
    assert observed["status"] == "logged"
    assert observed["callbackBeforeIO"] is True and observed["callbackMs"] < 100 * resident.factor
    assert observed["socketCalls"] == 2
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
    end = time.monotonic() + 3 * resident.factor
    while resident.runtime.active_count():
        assert time.monotonic() < end
        time.sleep(0.01)
    resident.runtime.open_admission()  # Fixture owner, never a client action.
    assert resident.observe(pending=first["pending"])["status"] == "logged"
    assert resident.counts["record"] == 1 and resident.counts["fsync"] == 2


def test_actual_control_refuses_wrong_nonce_and_missing_association(resident):
    binding = resident.session_dir / "worktree-binding.json"
    binding.write_text(json.dumps({**resident.subject, "creationNonce": "foreign"}), encoding="utf-8")
    assert resident.observe()["status"] in {"unavailable", "unknown"}
    binding.write_text(json.dumps({**resident.subject, "sessionId": "foreign"}), encoding="utf-8")
    assert resident.observe()["status"] == "unavailable"
    assert resident.counts["fsync"] == 0


def test_removed_registered_association_has_no_effect(resident):
    resident.record.sessions = [tracking.SessionEntry("different-head", "2026-01-02")]
    tracking.save_record(resident.record, resident.record_path)
    assert resident.observe()["status"] == "unknown"
    assert resident.counts["fsync"] == 0
    assert resident.runtime.active_count() == 0


def test_actual_lock_contention_expires_without_append_or_queued_waiter(resident):
    path = handoff_trace.trace_path(resident.record.repo, resident.record.worktree_id)
    with handoff_trace._append_lock(path.with_suffix(path.suffix + ".lock")):
        first = resident.observe()
        assert first["status"] == "unknown"
        assert first["reason"] == "lock-admission-timeout"
        assert resident.runtime.active_count() == 0
        assert resident.counts["fsync"] == 0
    assert resident.observe(pending=first["pending"])["status"] == "absent"
    assert resident.counts["record"] == 1
    assert resident.observe()["status"] == "logged"


@pytest.mark.parametrize("limit", ["rows", "bytes", "record", "elapsed"])
def test_total_scan_budgets_through_actual_server(resident, monkeypatch, limit):
    path = handoff_trace.trace_path(resident.record.repo, resident.record.worktree_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    reason = "scan-limit"
    if limit == "rows":
        path.write_bytes(b"{}\n" * (handoff_prompt_receipt.MAX_SCAN_ROWS + 1))
    elif limit == "bytes":
        row = json.dumps({"noise": "x" * 500}).encode() + b"\n"
        path.write_bytes(row * 1100)
        (resident.root / "logs" / "activity.jsonl").write_bytes(row * 1100)
    elif limit == "record":
        path.write_bytes(b"x" * (64 * 1024 + 1))
        reason = "record-limit"
    else:
        path.write_bytes(b"{}\n")
        loads = handoff_prompt_receipt.json.loads

        def slow_loads(data, *args, **kwargs):
            if threading.current_thread() is not threading.main_thread() and data == b"{}\n":
                time.sleep(0.21 * resident.factor)
            return loads(data, *args, **kwargs)

        monkeypatch.setattr(handoff_prompt_receipt.json, "loads", slow_loads)
        reason = "scan-timeout"
    first = resident.observe()
    assert first["status"] == "unknown" and first["reason"] == reason
    assert resident.counts["fsync"] == 0
    assert resident.runtime.active_count() == 0


def test_cancel_before_send_has_no_request_or_effect(resident):
    assert resident.observe(cancel_before=True)["status"] == "unavailable"
    assert resident.requests == []
    assert resident.counts["record"] == resident.counts["fsync"] == 0


def test_cancel_after_admission_retains_uncertain_commit(resident):
    resident.delay_sync = True
    first = resident.observe(cancel_on_fsync=True)
    assert resident.hold_sync.is_set() and first["status"] == "unknown"
    assert resident.runtime.active_count() == 1
    # An exact query cannot report absent while this append still owns fsync.
    assert resident.observe(pending=first["pending"])["status"] == "unknown"
    assert resident.counts["record"] == 1 and resident.runtime.active_count() == 1
    resident.release_sync.set()
    resident.runtime.wait_idle()
    assert resident.observe(pending=first["pending"])["status"] == "logged"
    assert resident.counts["fsync"] == 2


def test_storage_failure_is_unknown_and_never_blindly_retried(resident, monkeypatch):
    fsync = handoff_prompt_receipt.os.fsync

    def fail(_fd):
        raise OSError("fixture-private-detail")

    monkeypatch.setattr(handoff_prompt_receipt.os, "fsync", fail)
    first = resident.observe()
    assert first["status"] == "unknown" and first["reason"] == "commit-unknown"
    assert "fixture-private-detail" not in json.dumps(first)
    assert resident.observe(pending=first["pending"])["status"] == "unknown"
    assert resident.counts["record"] == resident.counts["query"] == 1
    monkeypatch.setattr(handoff_prompt_receipt.os, "fsync", fsync)
    assert resident.observe(pending=first["pending"])["status"] == "logged"
    assert resident.counts["record"] == 1 and resident.counts["query"] == 2
    assert len(handoff_trace.read_trace(resident.record.repo, resident.record.worktree_id)) == 1


def test_same_installation_cutover_reconciles_but_new_incarnation_refuses(resident):
    resident.drop_reply = True
    first = resident.observe()
    assert first["status"] == "unknown"
    resident.drop_reply = False
    resident.owner["routeGeneration"] = 2
    resident.owner["runtimeGeneration"] = "replacement-runtime"
    route = json.loads(resident.route_path.read_text(encoding="utf-8"))
    route["active"]["generation"] = 2
    resident.route_path.write_text(json.dumps(route), encoding="utf-8")
    assert resident.observe(pending=first["pending"])["status"] == "logged"
    resident.record.creation_nonce = "replacement-incarnation"
    tracking.save_record(resident.record, resident.record_path)
    assert resident.observe(pending=first["pending"])["status"] == "unknown"
    assert resident.counts["record"] == 1 and resident.counts["query"] == 2
    assert resident.counts["fsync"] == 2


@pytest.mark.parametrize("fault", [
    "missing-token", "wrong-token", "missing-nonce", "previous-only", "non-loopback",
    "source-switch", "unsupported-contract", "missing-capability",
])
def test_unavailable_inputs_have_no_effect_or_wake(resident, monkeypatch, fault):
    if fault == "missing-token":
        resident.token_path.unlink()
    elif fault == "wrong-token":
        resident.token_path.write_text("incorrect-token", encoding="utf-8")
    elif fault == "missing-nonce":
        binding = {key: value for key, value in resident.subject.items() if key != "creationNonce"}
        (resident.session_dir / "worktree-binding.json").write_text(json.dumps(binding), encoding="utf-8")
    elif fault in {"previous-only", "non-loopback"}:
        route = json.loads(resident.route_path.read_text(encoding="utf-8"))
        if fault == "previous-only":
            route["previous"] = route.pop("active")
        else:
            route["active"]["bind"] = "example.invalid"
        resident.route_path.write_text(json.dumps(route), encoding="utf-8")
    elif fault == "source-switch":
        resident.owner["sourceFingerprint"] = "replacement-source"
    else:
        health = resident.runtime.health()
        if fault == "unsupported-contract":
            health["promptReceipt"]["contractVersion"] = 2
        else:
            health["capabilities"] = []
        monkeypatch.setattr(resident.runtime, "health", lambda: health)
    assert resident.observe()["status"] == "unavailable"
    assert resident.counts["record"] == resident.counts["query"] == resident.counts["fsync"] == 0
    assert all(action == "health" for action in resident.requests)


def test_independent_locators_record_independently(resident):
    assert resident.observe(locator="task:first")["status"] == "logged"
    assert resident.observe(locator="file:second")["status"] == "logged"
    assert resident.counts["record"] == resident.counts["fsync"] == 2
    assert len(handoff_trace.read_trace(resident.record.repo, resident.record.worktree_id)) == 2


def test_unqualified_legacy_receipt_is_not_backfilled(resident):
    path = handoff_trace.trace_path(resident.record.repo, resident.record.worktree_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "event": handoff_prompt_receipt.EVENT, "worktree_id": resident.record.worktree_id,
        "session_id": resident.subject["sessionId"], "locator": "task:native-fixture",
    }) + "\n", encoding="utf-8")
    before = path.read_bytes()
    first = resident.observe()
    assert first["status"] == "unknown" and first["reason"] == "legacy-incarnation-unavailable"
    assert path.read_bytes() == before and resident.counts["fsync"] == 0


@pytest.fixture
def registration_resident(monkeypatch_config, request):
    return request.getfixturevalue("resident")


def test_actual_registration_producer_composes_with_resident_and_node(registration_resident, monkeypatch):
    from agent_worktrees import sessions

    resident = registration_resident
    load_provider("handoff_diagnostics", monkeypatch)
    load_provider("worktree_trust", monkeypatch)
    producer = load_provider("session_binding_cli", monkeypatch)
    config.set_active_project(resident.record.repo)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: resident.session_dir.parent)
    # The fixture owns no production lifecycle services or real trusted checkout.
    # Preserve the actual registration, sidecar, uncached reader and atomic stamp.
    monkeypatch.setattr(producer, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(producer, "_register_manager_owned_monitor_session", lambda *a, **kw: True)
    monkeypatch.setattr(producer.worktree_trust, "ensure_record_trusted", lambda _record: None)
    monkeypatch.setattr(producer.profile_assignment, "bind", lambda *a, **kw: None)
    monkeypatch.setattr(producer.profile_assignment, "maintain", lambda: None)
    current = tracking._load_record_uncached(resident.record_path)
    tracking.set_head_session(current, "different-head", save=False)
    tracking.save_record(current, resident.record_path)
    binding = resident.session_dir / "worktree-binding.json"
    binding.unlink()
    assert producer.cmd_register_session(SimpleNamespace(
        worktree_id=current.worktree_id, session_id=resident.subject["sessionId"],
        pane="%fixture", resident_environment=True,
    )) == 0
    projected = json.loads(binding.read_text(encoding="utf-8"))
    assert projected["project"] == current.repo
    assert projected["creationNonce"] == current.creation_nonce
    assert projected["sessionId"] == resident.subject["sessionId"]
    assert tracking._load_record_uncached(resident.record_path).resolved_head_session == "different-head"
    observed = resident.observe(callback=True)
    assert observed["status"] == "logged"
    assert observed["callbackBeforeIO"] is True and observed["callbackMs"] < 100 * resident.factor
    assert observed["socketCalls"] == 2
    assert resident.counts["record"] == 1 and resident.counts["helpers"] == 0


def test_actual_subject_writer_projects_pending_candidate_and_omits_legacy(resident, monkeypatch):
    from agent_worktrees import sessions

    producer = load_provider("handoff_diagnostics", monkeypatch)
    config.set_active_project(resident.record.repo)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: resident.session_dir.parent)
    current = tracking._load_record_uncached(resident.record_path)
    current.sessions = [tracking.SessionEntry("different-head", "2026-01-02")]
    tracking.set_head_session(current, "different-head", save=False)
    current.handoffs = [tracking.SessionHandoff(
        ordinal=1, token="fixture-handoff", predecessor="different-head",
        state="pending", opened_at="2026-01-02", candidate=resident.subject["sessionId"],
    )]
    tracking.save_record(current, resident.record_path)
    projected = producer.stamp_session_state_worktree_binding(
        resident.subject["sessionId"], current.worktree_id, worktree_record=current,
    )
    assert projected["creationNonce"] == current.creation_nonce
    assert resident.observe()["status"] == "logged"
    assert tracking._load_record_uncached(resident.record_path).resolved_head_session == "different-head"
    legacy = producer.stamp_session_state_worktree_binding(
        resident.subject["sessionId"], current.worktree_id,
    )
    assert "creationNonce" not in legacy and "project" not in legacy
    assert resident.observe()["status"] == "unavailable"
    assert resident.counts["record"] == 1


def test_four_inflight_zero_queue_and_uncertain_identity_fence(resident):
    resident.hold_backend = True
    batch = resident.observe(concurrent=4, distinct=True)
    assert all(result["status"] == "unknown" for result in batch["results"])
    assert resident.runtime.active_count() == resident.counts["maxActive"] == 4
    assert resident.counts["record"] == 4
    assert resident.observe(locator="task:fifth")["status"] == "unknown"
    assert resident.counts["record"] == resident.runtime.active_count() == 4
    first = batch["results"][0]
    assert resident.observe(locator="task:native-fixture-0", pending=first["pending"])["status"] == "unknown"
    assert resident.counts["query"] == 0
    resident.runtime.close_admission()
    assert resident.runtime.active_count() == 4
    resident.release_backend.set()
    resident.runtime.wait_idle()
    assert resident.runtime.active_count() == 0 and resident.counts["record"] == 4
    assert resident.counts["fsync"] == 0


@pytest.mark.parametrize("frame_size", [8192, 8193])
def test_actual_server_receipt_request_frame_boundary(resident, frame_size):
    payload = {
        "contractVersion": 1, "operation": "record",
        "subject": {**resident.subject, "locator": "task:frame"},
        "expectedOwner": resident.owner, "remainingMs": 1000 * resident.factor,
    }
    data = json.dumps({
        "version": 1, "token": "fixture-token",
        "action": "handoff-prompt-receipt", "payload": payload,
    }).encode("utf-8")
    data += b" " * (frame_size - len(data) - 1) + b"\n"
    assert len(data) == frame_size
    with socket.create_connection(("127.0.0.1", resident.port), timeout=2 * resident.factor) as peer:
        peer.sendall(data)
        with peer.makefile("rb") as response:
            raw = response.readline(8193)
    assert len(raw) <= 8192
    result = json.loads(raw)["result"]
    assert result["status"] == ("received" if frame_size == 8192 else "unavailable")
    assert resident.counts["record"] == resident.counts["fsync"] == (frame_size == 8192)


def test_oversized_real_server_reply_retains_exact_commit_for_reconciliation(resident):
    resident.oversized_reply = True
    first = resident.observe()
    assert first["status"] == "unknown" and resident.counts["fsync"] == 1
    resident.oversized_reply = False
    assert resident.observe(pending=first["pending"])["status"] == "logged"
    assert resident.counts["record"] == resident.counts["query"] == 1
    assert len(handoff_trace.read_trace(resident.record.repo, resident.record.worktree_id)) == 1
