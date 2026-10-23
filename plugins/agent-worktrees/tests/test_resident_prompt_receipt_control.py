"""Actual control sockets, with synthetic owner state and exclusively owned threads."""

from contextlib import contextmanager
import json
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import handoff_prompt_receipt_control as receipts
from agent_worktrees import status_monitor_cutover as control

OWNER = {
    "installationId": "installation", "sourceFingerprint": "source",
    "marketplaceId": "marketplace", "pluginId": "agent-worktrees",
    "routeGeneration": 2, "pid": 123, "processStartTime": "456",
    "runtimeGeneration": "runtime",
}
SUBJECT = {
    "project": "project", "worktreeId": "worktree", "creationNonce": "nonce",
    "sessionId": "subject", "locator": "task:synthetic",
}


def request(operation="record", **overrides):
    return {
        "contractVersion": 1, "operation": operation, "subject": dict(SUBJECT),
        "expectedOwner": dict(OWNER), "remainingMs": 1900, **overrides,
    }


@contextmanager
def admission(subject, validate, deadline):
    validate()
    yield validate


def backend(*args, **kwargs):
    with kwargs["admission"]() as revalidate:
        revalidate()
        revalidate()
    return {"status": "received", "replayed": True, "reason": "replayed"}


@contextmanager
def serving(runtime):
    server = control.ControlServer(
        lambda action, payload: (
            runtime.health() if action == "health" else runtime.handle(payload)
            if action == receipts.ACTION else {"ok": False, "error": "unsupported-action"}
        ),
        token="fixture-token",
    )
    server.start()
    try:
        yield server
    finally:
        runtime.close_admission()
        server.close()
        runtime.wait_idle()
        assert runtime.active_count() == 0
        assert not server.thread.is_alive()


def wire(server, payload, *, token="fixture-token", version=1):
    frame = {
        "version": version, "token": token, "action": receipts.ACTION, "payload": payload,
    }
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as sock:
        sock.sendall(json.dumps(frame).encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = sock.recv(1024)
            if not chunk:
                break
            data.extend(chunk)
            assert len(data) <= receipts.FRAME_BYTES
    return json.loads(data)["result"] if data else None


def test_real_control_roundtrip_no_subprocess_or_token_creation(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("helper started"))
    monkeypatch.setattr(control, "load_or_create_control_token", lambda *a: pytest.fail("token created"))
    runtime = receipts.ReceiptControl(lambda: dict(OWNER), backend=backend, admission=admission)
    for _ in range(2):
        runtime.open_admission()
        with serving(runtime) as server:
            started = time.monotonic()
            result = wire(server, request())
            assert result["status"] == "received" and result["subject"] == SUBJECT
            assert result["owner"] == OWNER and result["replayed"]
            assert time.monotonic() - started < 2
            assert runtime.health()["capabilities"] == [receipts.CAPABILITY]


def test_unqualified_backend_never_ready_or_effectful():
    runtime = receipts.ReceiptControl(lambda: pytest.fail("owner discovery"))
    with serving(runtime) as server:
        assert runtime.health() == {
            "capabilities": [], "promptReceipt": {"contractVersion": 1, "status": "unavailable"},
        }
        assert wire(server, request())["status"] == "unavailable"


@pytest.mark.parametrize("marker", [None, False, True, 0, 2])
def test_loader_requires_qualified_backend_marker(monkeypatch, marker):
    monkeypatch.setattr(receipts.importlib, "import_module", lambda *a: SimpleNamespace(
        RESIDENT_RECEIPT_CONTRACT=marker, resident_prompt_receipt=backend,
    ))
    assert receipts._backend() is None


def test_missing_backend_does_not_touch_binding_hook_or_route(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts, "_backend", lambda: None)
    runtime = receipts.for_runtime(tmp_path, "runtime", lambda: True)
    assert runtime.health()["capabilities"] == []
    assert list(tmp_path.iterdir()) == []


def test_wrong_token_and_oversized_frame_never_reach_backend():
    runtime = receipts.ReceiptControl(
        lambda: dict(OWNER), backend=lambda *a, **k: pytest.fail("effect admitted"),
    )
    with serving(runtime) as server:
        assert wire(server, request(), token="wrong") is None
        result = wire(server, request(padding="x" * receipts.FRAME_BYTES))
        assert result["reason"] == "invalid-frame"
        assert wire(server, request(), version=True)["reason"] == "invalid-frame"


@pytest.mark.parametrize("payload", [
    request(contractVersion=2), request(operation="exec"), request(remainingMs=True),
    request(remainingMs=float("nan")), request(remainingMs=0),
    request(subject={**SUBJECT, "worktreeId": "../foreign"}),
    request(subject={**SUBJECT, "creationNonce": ""}),
    request(subject={**SUBJECT, "locator": "token:other"}),
    request(subject={**SUBJECT, "path": "foreign"}),
    request(expectedOwner={**OWNER, "routeGeneration": 3}),
    request(expectedOwner={**OWNER, "sourceFingerprint": "other"}),
])
def test_invalid_request_or_owner_refuses_without_effect(payload):
    runtime = receipts.ReceiptControl(
        lambda: dict(OWNER), backend=lambda *a, **k: pytest.fail("effect admitted"),
    )
    with serving(runtime) as server:
        assert wire(server, payload)["status"] == "unavailable"


def test_owner_switch_at_backend_admission_is_revalidated():
    owner = dict(OWNER)
    def change_before_guard(*args, **kwargs):
        owner["routeGeneration"] += 1
        with kwargs["admission"]():
            pytest.fail("changed owner admitted")
    runtime = receipts.ReceiptControl(lambda: dict(owner), backend=change_before_guard, admission=admission)
    with serving(runtime) as server:
        assert wire(server, request())["reason"] == "owner-changed"


def test_inflight_mutation_query_cancel_and_drain_keep_custody():
    entered, release = threading.Event(), threading.Event()
    committed = []
    def hold(*args, **kwargs):
        with kwargs["admission"]() as validate:
            validate()
            entered.set()
            assert release.wait(5)
            committed.append(args)
        return {"status": "received", "replayed": False, "reason": "recorded"}
    runtime = receipts.ReceiptControl(lambda: dict(OWNER), backend=hold, admission=admission)
    with serving(runtime) as server:
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        try:
            sock.sendall(json.dumps({
                "version": 1, "token": "fixture-token",
                "action": receipts.ACTION, "payload": request(remainingMs=20),
            }).encode() + b"\n")
            assert entered.wait(2)
            sock.close()
            assert runtime.active_count() == 1
            assert wire(server, request("query"))["status"] == "unknown"
            assert wire(server, request())["reason"] == "record-in-flight"
            runtime.close_admission()
            assert wire(server, request())["status"] == "unavailable"
            assert runtime.active_count() == 1
            assert runtime.health()["promptReceipt"]["status"] == "unavailable"
        finally:
            sock.close()
            release.set()
            runtime.wait_idle()
        assert len(committed) == 1


def test_four_inflight_zero_queue():
    release = threading.Event()
    barrier = threading.Barrier(5)
    def hold(*args, **kwargs):
        barrier.wait(timeout=3)
        assert release.wait(3)
        return {"status": "received", "replayed": False, "reason": "recorded"}
    runtime = receipts.ReceiptControl(lambda: dict(OWNER), backend=hold)
    workers = []
    try:
        for index in range(4):
            payload = request(subject={**SUBJECT, "locator": f"task:{index}"})
            worker = threading.Thread(target=runtime.handle, args=(payload,))
            workers.append(worker)
            worker.start()
        barrier.wait(timeout=3)
        assert runtime.active_count() == 4
        assert runtime.handle(request())["reason"] == "receipt-busy"
    finally:
        release.set()
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
    assert runtime.active_count() == 0


def test_completed_after_deadline_is_not_claimed_as_timely(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(receipts.time, "monotonic", lambda: now[0])
    def slow_commit(*a, **k):
        now[0] += 3
        return {"status": "received", "replayed": False, "reason": "recorded"}
    runtime = receipts.ReceiptControl(lambda: dict(OWNER), backend=slow_commit)
    assert runtime.handle(request())["status"] == "unknown"
    assert runtime.active_count() == 0


@pytest.mark.parametrize("operation", ["query", "record"])
def test_final_owner_validation_is_inside_the_deadline(monkeypatch, operation):
    now = [100.0]
    calls = []
    monkeypatch.setattr(receipts.time, "monotonic", lambda: now[0])
    def owner():
        calls.append(True)
        if len(calls) == 2:
            now[0] += 3
        return dict(OWNER)
    runtime = receipts.ReceiptControl(owner, backend=lambda *args, **kwargs: {
        "status": "received" if operation == "record" else "absent",
        "replayed": False, "reason": "completed",
    })
    result = runtime.handle(request(operation))
    assert result["status"] == "unknown"
    assert result["reason"] == "completion-after-deadline"
    assert runtime.active_count() == 0


@pytest.fixture
def subject_record(sample_record, tmp_tracking_dir, monkeypatch):
    from agent_worktrees import config, installer, tracking

    sample_record.creation_nonce = SUBJECT["creationNonce"]
    sample_record.sessions = [tracking.SessionEntry(SUBJECT["sessionId"], "2026-01-01")]
    path = tmp_tracking_dir / f"{sample_record.worktree_id}.yaml"
    tracking.save_record(sample_record, path)
    project = sample_record.repo
    monkeypatch.setattr(config, "tracking_dir", lambda project=None: tmp_tracking_dir)
    monkeypatch.setattr(installer, "read_projects_registry", lambda **kw: {
        "projects": {project: {}},
    })
    return {
        **SUBJECT, "project": sample_record.repo, "worktreeId": sample_record.worktree_id,
    }, sample_record, path


def test_real_record_guard_preserves_non_head_subject_and_excludes_other_writer(subject_record):
    from agent_worktrees import tracking

    subject, record, path = subject_record
    record.sessions.append(tracking.SessionEntry("another-head", "2026-01-02"))
    tracking.save_record(record, path)
    acquired = []
    def writer():
        with tracking._RecordLock(path, blocking=False, require_sidecar=True) as lock:
            acquired.append(lock.acquired)
    with receipts.subject_admission(subject, lambda: None, time.monotonic() + 2) as validate:
        validate()
        worker = threading.Thread(target=writer)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
    assert acquired == [False]


def test_exact_pending_successor_is_associated(subject_record):
    from agent_worktrees import tracking

    subject, record, path = subject_record
    subject["sessionId"] = "successor"
    record.handoffs = [tracking.SessionHandoff(
        ordinal=1, token="numbered", predecessor="old", state="pending",
        opened_at="2026-01-01", candidate="successor",
    )]
    tracking.save_record(record, path)
    with receipts.subject_admission(subject, lambda: None, time.monotonic() + 2):
        pass
    subject["sessionId"] = "unrelated"
    with pytest.raises(receipts.AdmissionUnavailable, match="association"):
        with receipts.subject_admission(subject, lambda: None, time.monotonic() + 2):
            pytest.fail("inferred successor admitted")


def test_record_from_another_project_is_not_admitted(subject_record):
    from agent_worktrees import tracking

    subject, record, path = subject_record
    record.repo = "foreign"
    tracking.save_record(record, path)
    with pytest.raises(receipts.AdmissionUnavailable, match="incarnation"):
        with receipts.subject_admission(subject, lambda: None, time.monotonic() + 2):
            pytest.fail("foreign project record admitted")


def test_nonce_and_association_revalidated_after_backend_wait(subject_record, monkeypatch):
    from agent_worktrees import tracking

    subject, record, path = subject_record
    original = tracking.load_record
    changed = [False]
    reads = []
    def load(p):
        current = original(p)
        reads.append((current.lifecycle_revision, current.head_revision))
        if changed[0]:
            current.creation_nonce = "replacement"
            current.lifecycle_revision += 1
        return current
    monkeypatch.setattr(tracking, "load_record", load)
    with receipts.subject_admission(subject, lambda: None, time.monotonic() + 2) as validate:
        changed[0] = True
        with pytest.raises(receipts.AdmissionUnavailable, match="incarnation"):
            validate()
    assert len(reads) == 2


@pytest.mark.parametrize("field,value", [
    ("project", "unknown"), ("worktreeId", "unknown"),
    ("creationNonce", "wrong"), ("sessionId", "unassociated"),
])
def test_unavailable_subject_does_not_enter_effect(subject_record, field, value):
    subject, _record, _path = subject_record
    subject[field] = value
    runtime = receipts.ReceiptControl(lambda: dict(OWNER), backend=backend)
    assert runtime.handle(request(subject=subject))["status"] == "unavailable"


def test_storage_error_after_admission_remains_unknown(subject_record):
    subject, _record, _path = subject_record
    def fail_after_admission(*args, **kwargs):
        with kwargs["admission"]() as validate:
            validate()
            raise OSError("synthetic-write-may-have-committed")
    runtime = receipts.ReceiptControl(lambda: dict(OWNER), backend=fail_after_admission)
    result = runtime.handle(request(subject=subject))
    assert result["status"] == "unknown"
    assert "synthetic-write-may-have-committed" not in json.dumps(result)


def test_contended_record_has_no_wait_queue(subject_record):
    from agent_worktrees import tracking

    subject, _record, path = subject_record
    release, held = threading.Event(), threading.Event()
    def holder():
        with tracking._RecordLock(path, require_sidecar=True):
            held.set()
            assert release.wait(4)
    worker = threading.Thread(target=holder)
    worker.start()
    try:
        assert held.wait(2)
        start = time.monotonic()
        with pytest.raises(receipts.AdmissionUnavailable, match="subject-busy"):
            with receipts.subject_admission(subject, lambda: None, time.monotonic() + 2):
                pytest.fail("contended admission")
        assert time.monotonic() - start < .5
    finally:
        release.set()
        worker.join(5)
        assert not worker.is_alive()


def test_real_binding_and_active_route_roundtrip_without_helper(tmp_path, monkeypatch):
    import subprocess

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    home = tmp_path / "home"
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-worktrees"
    payload_scripts = payload / "scripts"
    shutil.copytree(scripts / "installation-context", payload_scripts / "installation-context")
    shutil.copyfile(
        scripts / "resident_control_projection.py", payload_scripts / "resident_control_projection.py",
    )
    root = home / ".agent-worktrees"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "hook_client.py").write_text("# Deployed hook has no payload siblings.\n")
    (home / ".copilot" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.chdir(home)

    def load(name):
        spec = importlib.util.spec_from_file_location(
            "receipt_real_" + name, scripts / (name + ".py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    binding = load("command_binding")
    primitive = binding._load_primitive(payload)
    receipt = binding.publish_legacy_binding(
        primitive, payload_root=payload, plugin_id="agent-worktrees", legacy_root=root,
    )
    manifest = root / "deploy-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 3, "service": "agent-worktrees", "commandBinding": receipt,
    }))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("helper started"))
    monkeypatch.setattr(receipts, "_backend", lambda: backend)
    calls = []
    from agent_worktrees import _command_binding
    original_binding = _command_binding.resolve_command_binding
    def observed_binding(*args, **kwargs):
        calls.append(args)
        return original_binding(*args, **kwargs)
    monkeypatch.setattr(_command_binding, "resolve_command_binding", observed_binding)
    runtime = receipts.for_runtime(root, "runtime", lambda: True, port=lambda: server.port)
    runtime._admission = admission
    route_path = root / "status-monitor-routing" / "active.json"
    route_path.parent.mkdir()
    with serving(runtime) as server:
        active = {
            "bind": "127.0.0.1", "port": server.port, "pid": os.getpid(),
            "generation": 5, "process_start_time": str(control.locks.process_start_time(os.getpid())),
        }
        route_path.write_text(json.dumps({"active": active}))
        started = time.monotonic()
        health = runtime.health()["promptReceipt"]
        assert health["status"] == "ready"
        result = wire(server, request(expectedOwner=health["owner"]))
        assert result["status"] == "received"
        assert time.monotonic() - started < 2
        assert len(calls) == 7  # startup, health, admission, guard, two checkpoints, completion
        route_path.write_text(json.dumps({"previous": active}))
        assert runtime.health()["promptReceipt"]["status"] == "unavailable"
        route_path.write_text(json.dumps({"active": {**active, "port": server.port + 1}}))
        assert runtime.health()["promptReceipt"]["status"] == "unavailable"
        route_path.write_text(json.dumps({"active": active}))
        def changed_binding(*args, **kwargs):
            value = observed_binding(*args, **kwargs)
            value["source"]["fingerprint"] = "replacement-source"
            value["installation"]["sourceFingerprint"] = "replacement-source"
            return value
        monkeypatch.setattr(_command_binding, "resolve_command_binding", changed_binding)
        assert runtime.health()["promptReceipt"]["status"] == "unavailable"
        assert not (root / "status-monitor-control.token").exists()
