"""The owner adapter's native state-writer handoff, without service opt-in."""

from __future__ import annotations

import threading
import time
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_pull_requests.watch_contract import MERGED, PRSnapshot
from agent_pull_requests.watch_storage import read_subscriptions_state

pytestmark = pytest.mark.contract("agent_pull_requests.watch.writer_handoff")


def registration(identity="one"):
    return {
        "repo": "example/project", "number": 1, "subscriber_id": identity,
        "until": [MERGED], "notify": {},
    }


@pytest.fixture
def owners(tmp_path):
    from agent_pull_requests.watch_writer import WatchWriter

    created = []

    def create(*, root=None, authorize=None, **kwargs):
        owner = WatchWriter(
            root=root or tmp_path, authorize=authorize or (lambda _: None),
            fetch=lambda *_: PRSnapshot(pr_state="open"), poll_interval=0.01,
            **kwargs,
        )
        created.append(owner)
        return owner

    yield create
    for owner in reversed(created):
        owner.close(timeout=3)


def test_passive_does_not_acquire_read_or_write_state(owners, tmp_path):
    path = tmp_path / "watch-subscriptions.json"
    path.write_text("not valid JSON", encoding="utf-8")
    owner = owners()
    assert not owner.writer_held and not owner.read_ready
    assert owner.compute("register", registration())["retryable"] is True
    assert path.read_text(encoding="utf-8") == "not valid JSON"


def test_adoption_requires_actual_native_writer_acquisition(owners, tmp_path):
    incumbent = SingleInstance(tmp_path, service="agent-pull-requests-watch")
    incumbent.acquire()
    owner = owners()
    try:
        with pytest.raises(AlreadyRunningError):
            owner.adopt(timeout=1)
        assert not owner.writer_held and not owner.read_ready
        assert owner.compute("register", registration())["retryable"] is True
        assert not (tmp_path / "watch-subscriptions.json").exists()
    finally:
        incumbent.release()


def test_adoption_and_effect_authorization_are_separate(owners, tmp_path):
    owner = owners()
    owner.adopt(timeout=1)
    assert owner.writer_held and owner.read_ready
    assert owner.compute("register", registration())["retryable"] is True
    assert not (tmp_path / "watch-subscriptions.json").exists()
    owner.activate(timeout=1)
    assert owner.compute("register", registration())["registered"] is True
    assert read_subscriptions_state(tmp_path)[0]["subscriber_id"] == "one"


def test_handoff_reads_final_state_only_after_predecessor_release(owners, tmp_path):
    old, new = owners(), owners()
    old.adopt(timeout=1)
    old.activate(timeout=1)
    assert old.compute("register", registration("earlier"))["registered"]
    with pytest.raises(AlreadyRunningError):
        new.adopt(timeout=1)
    assert old.compute("unregister", registration("earlier"))["unregistered"]
    assert old.compute("register", registration("latest"))["registered"]
    old.drain_and_release(timeout=1)
    final = read_subscriptions_state(tmp_path)
    assert not old.writer_held
    new.adopt(timeout=1)
    assert new.writer_held and new.read_ready
    assert new.compute("status", {})["subscribers"] == {"example/project#1": ["latest"]}
    assert read_subscriptions_state(tmp_path) == final
    assert new.compute("register", registration())["retryable"] is True


def test_incomplete_drain_retains_lease_and_same_registry_for_resume(owners, monkeypatch):
    old, new = owners(), owners()
    old.adopt(timeout=1)
    old.activate(timeout=1)
    old.compute("register", registration("accepted"))
    registry = old.daemon._registry
    quiesce = old.daemon.quiesce

    def incomplete(timeout):
        quiesce(timeout)
        raise TimeoutError("controlled incomplete drain")

    monkeypatch.setattr(old.daemon, "quiesce", incomplete)
    with pytest.raises(TimeoutError, match="incomplete drain"):
        old.drain_and_release(timeout=1)
    assert old.writer_held
    with pytest.raises(AlreadyRunningError):
        new.adopt(timeout=1)
    old.resume(timeout=1)
    assert old.daemon._registry is registry
    assert old.compute("status", {})["subscribers"] == {"example/project#1": ["accepted"]}
    assert old.compute("register", registration("later"))["registered"]


def test_resume_after_transfer_reacquires_and_reloads_final_state(owners, tmp_path, monkeypatch):
    old, new = owners(), owners()
    old.adopt(timeout=1)
    old.activate(timeout=1)
    old.compute("register", registration("old"))
    registry = old.daemon._registry
    old.drain_and_release(timeout=1)
    new.adopt(timeout=1)
    new.activate(timeout=1)
    new.compute("unregister", registration("old"))
    new.compute("register", registration("new"))
    with pytest.raises(AlreadyRunningError):
        old.resume(timeout=1)
    assert not old.writer_held
    new.close(timeout=1)
    final = read_subscriptions_state(tmp_path)
    activate = old.daemon.resume_effects

    def inspect_before_effects(timeout):
        assert old.daemon._registry.snapshot_state() == final
        activate(timeout)

    monkeypatch.setattr(old.daemon, "resume_effects", inspect_before_effects)
    old.resume(timeout=1)
    assert old.writer_held and old.daemon._registry is not registry
    assert old.compute("status", {})["subscribers"] == {"example/project#1": ["new"]}


@pytest.mark.parametrize("operation", ["adopt", "activate", "drain", "release", "resume"])
def test_authority_is_rechecked_at_each_writer_transition(owners, operation, tmp_path):
    calls = []
    refused = []

    def authorize(action):
        calls.append(action)
        if action in refused:
            raise PermissionError("controlled authority refusal")

    owner = owners(authorize=authorize)
    if operation != "adopt":
        owner.adopt(timeout=1)
    if operation in {"drain", "release", "resume"}:
        owner.activate(timeout=1)
    if operation == "resume":
        owner.drain_and_release(timeout=1)
    refused.append(operation)
    method = (
        owner.drain_and_release if operation in {"drain", "release"}
        else getattr(owner, operation)
    )
    with pytest.raises(PermissionError, match="authority refusal"):
        method(timeout=1)
    assert calls[-1] == operation
    assert owner.writer_held == (operation not in {"adopt", "resume"})
    if operation == "release":
        assert owner.daemon.quiescent
    if operation == "adopt":
        assert not (tmp_path / "watch-subscriptions.json").exists()


def test_root_is_bound_without_mutating_ambient_legacy_override(owners, tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    a, b = owners(root=first), owners(root=second)
    for owner in (a, b):
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
    assert a.compute("register", registration("a"))["registered"]
    assert b.compute("register", registration("b"))["registered"]
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path / "unrelated"))
    assert a.compute("register", registration("a2"))["registered"]
    a.drain_and_release(timeout=1)
    assert {entry["subscriber_id"] for entry in read_subscriptions_state(first)} == {"a", "a2"}
    assert {entry["subscriber_id"] for entry in read_subscriptions_state(second)} == {"b"}
    assert not (tmp_path / "unrelated").exists()


def test_failed_read_retains_native_lease_but_cannot_authorize_effects(owners, tmp_path):
    path = tmp_path / "watch-subscriptions.json"
    path.write_text("{}", encoding="utf-8")
    owner, other = owners(), owners()
    with pytest.raises(ValueError, match="persisted watch"):
        owner.adopt(timeout=1)
    assert owner.writer_held and not owner.read_ready
    with pytest.raises(RuntimeError, match="read-ready"):
        owner.activate(timeout=1)
    with pytest.raises(AlreadyRunningError):
        other.adopt(timeout=1)
    path.write_text("[]", encoding="utf-8")
    owner.adopt(timeout=1)
    owner.activate(timeout=1)
    assert owner.compute("register", registration())["registered"]


def test_failed_close_retains_writer_until_all_workers_stop(owners, monkeypatch):
    owner, other = owners(), owners()
    owner.adopt(timeout=1)
    owner.activate(timeout=1)
    close = owner.daemon.close

    def incomplete(timeout):
        raise TimeoutError("controlled live worker")

    monkeypatch.setattr(owner.daemon, "close", incomplete)
    with pytest.raises(TimeoutError, match="live worker"):
        owner.close(timeout=1)
    assert owner.writer_held
    with pytest.raises(AlreadyRunningError):
        other.adopt(timeout=1)
    monkeypatch.setattr(owner.daemon, "close", close)
    owner.close(timeout=1)
    assert not owner.writer_held
    other.adopt(timeout=1)


def test_lifecycle_lock_contention_uses_completion_budget(owners):
    owner = owners()
    entered, proceed = threading.Event(), threading.Event()
    errors = []

    def authorize(operation):
        entered.set()
        assert proceed.wait(3)

    owner.authorize = authorize

    def adopt():
        try:
            owner.adopt(timeout=2)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=adopt)
    worker.start()
    try:
        assert entered.wait(1)
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            owner.adopt(timeout=0.03)
        assert time.monotonic() - start < 0.5
    finally:
        proceed.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and errors == []


def test_passive_resume_and_late_shutdown_do_not_reopen_admission(owners):
    owner = owners()
    with pytest.raises(RuntimeError, match="predecessor"):
        owner.resume(timeout=1)
    owner.close(timeout=1)
    with pytest.raises(RuntimeError, match="closed"):
        owner.adopt(timeout=1)
    assert owner.compute("register", registration())["retryable"] is True


@pytest.mark.parametrize("result", [False, True, {}, "ready"])
def test_incomplete_authority_result_never_grants_writer_ownership(owners, result):
    owner = owners(authorize=lambda _: result)
    with pytest.raises(PermissionError, match="validation did not complete"):
        owner.adopt(timeout=1)
    assert not owner.writer_held and not owner.read_ready


@pytest.mark.parametrize("operation", ["compute", "writer_held", "read_ready", "close"])
def test_inherited_process_refuses_before_touching_parent_objects(owners, monkeypatch, operation):
    from agent_pull_requests import watch_writer

    owner = owners()
    owner.adopt(timeout=1)
    owner.activate(timeout=1)
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(watch_writer.os, "getpid", lambda: owner._creating_pid + 1)
        patch.setattr(owner.daemon, "compute", lambda *_: calls.append("compute"))
        patch.setattr(owner.daemon, "close", lambda **_: calls.append("close"))
        with pytest.raises(RuntimeError, match="another process"):
            if operation == "compute":
                owner.compute("register", registration())
            elif operation == "close":
                owner.close(timeout=1)
            else:
                getattr(owner, operation)
    assert calls == [] and owner.writer_held


_CHILD = """
import sys, time
from pathlib import Path
from agent_pull_requests.watch_writer import WatchWriter
from agent_pull_requests.watch_contract import PRSnapshot

root = Path(sys.argv[1])
writer = WatchWriter(
    root=root, authorize=lambda _: None, fetch=lambda *_: PRSnapshot(pr_state="open"),
    poll_interval=0.01,
)
try:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    assert writer.compute("register", {
        "repo": "example/project", "number": 1, "subscriber_id": "child",
        "until": ["merged"], "notify": {},
    })["registered"]
    (root / "ready").write_text("ready")
    deadline = time.monotonic() + 8
    while not (root / "drain").exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    writer.drain_and_release(timeout=1)
    (root / "released").write_text("released")
    while not (root / "exit").exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
finally:
    writer.close(timeout=2)
"""


def test_real_process_handoff_preserves_final_state_before_incumbent_exit(owners, tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(root)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )

    def wait_for(name):
        deadline = time.monotonic() + 3
        while not (root / name).exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(0.01)
        assert (root / name).exists()

    successor = owners(root=root)
    try:
        wait_for("ready")
        with pytest.raises(AlreadyRunningError):
            successor.adopt(timeout=1)
        (root / "drain").write_text("drain", encoding="utf-8")
        wait_for("released")
        assert child.poll() is None
        final = read_subscriptions_state(root)
        successor.adopt(timeout=1)
        assert successor.writer_held
        assert successor.daemon._registry.snapshot_state() == final
        assert successor.compute("status", {})["subscribers"] == {"example/project#1": ["child"]}
        assert read_subscriptions_state(root) == final
    finally:
        (root / "drain").write_text("drain", encoding="utf-8")
        (root / "exit").write_text("exit", encoding="utf-8")
        try:
            stdout, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=3)
            pytest.fail(f"owned writer fixture did not exit: {stdout!r} {stderr!r}")
    assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert not (root.parent / "watch-subscriptions.json").exists()
