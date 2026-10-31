"""Heap-only creation notice decisions; real ledgers, Git and stream handles are integration."""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import FunctionType, ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-worktrees/src/agent_worktrees"


@pytest.fixture
def api(monkeypatch):
    name = "promotion_creation_notices"
    package = ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)
    state = SimpleNamespace(events=[], order=[], persisted=[], notice_error=None, storage_error=None)

    def stub(child, **fields):
        module = ModuleType(f"{name}.{child}")
        module.__dict__.update(fields)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        setattr(package, child, module)
        return module

    def load(child):
        spec = importlib.util.spec_from_file_location(f"{name}.{child}", SOURCE / f"{child}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        setattr(package, child, module)
        spec.loader.exec_module(module)
        return module

    def log_event(event, **fields):
        state.events.append({"event": event, **fields})

    class Sink:
        def write(self, text):
            state.order.append("notice")
            if state.notice_error is not None:
                raise state.notice_error
            return len(text)

        def flush(self):
            state.order.append("flush")

    sink = Sink()

    class Human:
        def __init__(self, label, *, stream=None):
            self.stream = sink if stream is None else stream

        def __enter__(self):
            self.progress("starting")
            return self

        def __exit__(self, *error):
            pass

        def progress(self, message):
            self.stream.write(message)
            self.stream.flush()

        def complete(self, outcome):
            self.progress(outcome)

    stub("activity", log_event=log_event)
    stub("output", HumanOperation=Human, current_operation=lambda: None,
         human_operation=lambda label, enabled=True: Human(label) if enabled else nullcontext())
    diagnostics = load("creation_diagnostics")
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(stderr=sink))
    record = SimpleNamespace(resources=[])
    owner_path = SimpleNamespace(exists=lambda: True)

    class Lock:
        def __init__(self, path, **options):
            assert path is owner_path and options == {"require_sidecar": True}

        def __enter__(self):
            state.order.append("lock")

        def __exit__(self, *error):
            state.order.append("unlock")

    def read_record(path):
        assert path is owner_path
        state.order.append("load")
        return record

    def add_claim(owner, claim, *, save):
        assert owner is record and save is False
        state.order.append("claim")
        record.resources.append(claim)

    def save_record(owner, path):
        assert owner is record and path is owner_path
        state.order.append("persist")
        if state.storage_error is not None:
            raise state.storage_error
        state.persisted[:] = record.resources

    stub("config")
    stub("obligations", ACTIVE="active")
    stub("tracking", _RecordLock=Lock, load_record=read_record, add_resource_claim=add_claim,
         save_record=save_record, ResourceClaim=lambda **fields: SimpleNamespace(**fields),
         _now_iso=lambda: "synthetic-time",
         format_claim_ref=lambda machine, project, worktree: f"{machine}/{project}/{worktree}")
    stub("creation_identity", require_same_machine_identity=lambda *args: None)
    stub("execution_spaces", ExecutionSpaceError=ValueError,
         current_execution_context=lambda config: SimpleNamespace(execution_machine="unit"),
         project_record_config=lambda owner: SimpleNamespace(),
         require_owner_identity=lambda *args: True, require_record_mutation=lambda *args: None)
    stub("worktree_machine_identity", WorktreeMachineIdentity=object)
    lazy = ModuleType("lazy_cli_dispatch")
    lazy.self_override = lambda owner_globals, key, default: owner_globals.get(key, default)
    monkeypatch.setitem(sys.modules, lazy.__name__, lazy)
    owner = load("creation_owner_claim")
    monkeypatch.setattr(owner, "sys", SimpleNamespace(stderr=sink))

    def owner_notice(text, **options):
        assert options["file"] is owner.sys.stderr
        owner.sys.stderr.write(text)

    notice = FunctionType(owner_notice.__code__, owner.journal_owner_claim.__globals__,
                          closure=owner_notice.__closure__)
    assert notice.__globals__ is vars(owner)
    monkeypatch.setitem(vars(owner), "print", notice)

    def claim():
        return owner.journal_owner_claim(
            SimpleNamespace(machine="unit", repo_name="project"), "child", "unit/project/parent",
            resolve_owner=lambda *args: (owner_path, "parent", None),
        )

    progress = load("launch_progress")
    monkeypatch.setattr(progress, "time", SimpleNamespace(monotonic=lambda: 0))
    monkeypatch.setattr(progress, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="scope")))

    class Stop:
        def set(self):
            state.order.append("stop")

        def wait(self, timeout):
            pytest.fail("heap unit must not execute a heartbeat wait")

    class Thread:
        ident = 1

        def __init__(self, **options):
            self.target = options["target"]

        def start(self):
            state.order.append("thread-boundary")

        def join(self):
            state.order.append("join-boundary")

    monkeypatch.setattr(progress, "threading", SimpleNamespace(Event=Stop, Thread=Thread))
    return SimpleNamespace(diagnostics=diagnostics, owner=owner, progress=progress,
                           state=state, claim=claim)


def test_successful_advisory_calls_once_without_diagnostic(api):
    work = []
    api.diagnostics.emit("branch_notice", lambda: work.append("written"), worktree_id="child")
    assert work == ["written"]
    assert api.state.events == []


def test_failed_advisory_records_only_sanitized_numeric_evidence(api):
    error = OSError(19, "synthetic-secret", "synthetic-private-path")
    error.winerror = 9
    calls = []

    def write():
        calls.append("attempt")
        raise error

    api.diagnostics.emit("branch_notice", write, worktree_id="child")
    assert calls == ["attempt"]
    assert api.state.events == [{
        "event": "creation_diagnostic", "worktree_id": "child", "scope": "branch_notice",
        "error_type": "OSError", "os_errno": 19, "winerror": 9,
    }]
    assert "synthetic-secret" not in json.dumps(api.state.events)
    assert "synthetic-private-path" not in json.dumps(api.state.events)


def test_non_io_callback_failure_remains_the_original_error(api):
    error = ValueError("work failure")

    def write():
        raise error

    with pytest.raises(ValueError) as caught:
        api.diagnostics.emit("branch_notice", write)
    assert caught.value is error
    assert api.state.events == []


def test_claim_acceptance_follows_persistence_before_failed_notice(api):
    api.state.notice_error = OSError(19, "synthetic-secret", "synthetic-private-path")
    assert api.claim() is True
    assert api.state.order == ["lock", "load", "claim", "persist", "unlock", "notice"]
    assert len(api.state.persisted) == 1
    assert api.state.persisted[0].ref == "unit/project/child"
    assert [event["scope"] for event in api.state.events] == ["owner_claim_notice"]
    assert "synthetic-secret" not in json.dumps(api.state.events)
    assert "synthetic-private-path" not in json.dumps(api.state.events)


def test_persistence_rejection_stays_distinct_from_notice_failure(api):
    api.state.storage_error = OSError(28, "storage-secret", "storage-private-path")
    api.state.notice_error = OSError(19, "notice-secret", "notice-private-path")
    assert api.claim() is False
    assert api.state.order == ["lock", "load", "claim", "persist", "unlock", "notice"]
    assert api.state.persisted == []
    assert [(event["scope"], event["os_errno"]) for event in api.state.events] == [
        ("owner_claim_persistence", 28), ("owner_claim_notice", 19),
    ]
    text = json.dumps(api.state.events)
    assert all(marker not in text for marker in
               ("storage-secret", "storage-private-path", "notice-secret", "notice-private-path"))


def test_creation_progress_notice_failure_preserves_original_work_error(api):
    api.state.notice_error = OSError(19, "notice-secret")
    error = OSError(28, "synthetic Git actor failure")
    with pytest.raises(OSError) as caught:
        with api.progress.step("Creating Git worktree", worktree_id="child", creation_advisory=True):
            api.state.order.append("work")
            raise error
    assert caught.value is error
    assert api.state.order == ["notice", "flush", "thread-boundary", "work", "stop",
                               "join-boundary", "notice", "flush"]
    phases = [event for event in api.state.events if event["event"] == "launch_preparation"]
    assert [event["state"] for event in phases] == ["started", "failed"]
    assert phases[-1]["error_type"] == "OSError"
    notices = [event for event in api.state.events if event["event"] == "creation_diagnostic"]
    assert len(notices) == 2 and all(event["os_errno"] == 19 for event in notices)
    assert "notice-secret" not in json.dumps(api.state.events)
    assert "synthetic Git actor failure" not in json.dumps(api.state.events)


def test_default_progress_still_refuses_failed_initial_output(api):
    api.state.notice_error = OSError(19, "default output failure")
    with pytest.raises(OSError) as caught:
        with api.progress.step("Ordinary progress", worktree_id="child"):
            pytest.fail("default progress must not admit work after output startup failure")
    assert caught.value is api.state.notice_error
    assert api.state.order == ["notice"]
    assert [event["state"] for event in api.state.events] == ["started"]
