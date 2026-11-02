"""Review regressions at atomic creation and an already-launched script."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.config import Config
from agent_dispatch.script_worker import ScriptTaskRuntime
from agent_dispatch.server import build_app
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests.test_action_worker_admission import _policy
from tests.test_mcp_http import _boot
from tests.test_script_worker import FakeClient
from tests.test_supervisor import QueueBackedClient, _ok_spawn


def test_create_claim_denial_is_atomic_but_enqueue_and_dedup_remain_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    with TestClient(app) as api:
        queued = api.post("/tasks", json={
            "title": "ordinary enqueue", "repo": TEST_REPO, "dedup_key": "existing",
        })
        assert queued.status_code == 200 and queued.json()["status"] == "queued"
        duplicate = api.post("/tasks", json={
            "title": "ordinary enqueue", "repo": TEST_REPO, "dedup_key": "existing",
            "claim_as": "host/wt",
        })
        assert duplicate.status_code == 200 and duplicate.json()["id"] == queued.json()["id"]
        before = app.state.queue.list(repo=TEST_REPO)
        body = {"title": "new acquisition", "repo": TEST_REPO,
                "dedup_key": "new", "claim_as": "host/wt"}
        denied = api.post("/tasks", json=body)
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "action_inhibited"
        assert app.state.queue.list(repo=TEST_REPO) == before
        _policy(root, False)
        acquired = api.post("/tasks", json=body)
        assert acquired.status_code == 200 and acquired.json()["status"] == "claimed"
        assert acquired.json()["attempts"] == 1


def test_launched_script_waits_for_clear_without_abandoning_pending_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, False)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    queue = app.state.queue
    task = queue.create("script work", repo=TEST_REPO)
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="lane")
    queue.record_spawn(
        reservation.key,
        session_handle='script-body:{"worker_id":"script-one","pid":4321,"start_token":"token"}',
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    pending = queue.get_reservation(reservation.key)
    denied = threading.Event()
    errors: list[BaseException] = []
    url, stop = _boot(app)

    class ObservedClient(DispatchClient):
        def claim(self, **kwargs: object) -> dict | None:
            try:
                return super().claim(**kwargs)
            except DispatchError:
                denied.set()
                raise

    runtime = ScriptTaskRuntime(
        ObservedClient(url), task.id, "script-one", repo=TEST_REPO, heartbeat_seconds=0,
    )
    runtime.admission_retry_seconds = 0.01

    def run() -> None:
        try:
            with runtime as worker:
                worker.announce_start()
                worker.report_success({"summary": "done"})
        except BaseException as exc:
            errors.append(exc)

    _policy(root, True)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert denied.wait(5)
        assert thread.is_alive()
        assert queue.get(task.id).status == "queued"
        assert queue.get(task.id).attempts == 0
        assert queue.get_reservation(reservation.key) == pending
        _policy(root, False)
        thread.join(5)
        assert not thread.is_alive() and not errors
        assert queue.get(task.id).status in {"submitted", "completed"}
        assert queue.get(task.id).attempts == 1
        assert queue.get_reservation(reservation.key).key == pending.key
    finally:
        _policy(root, False)
        thread.join(5)
        stop()


def test_create_claim_rechecks_and_rolls_back_a_partially_written_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, False)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    queue = app.state.queue
    audit = queue._audit

    def audit_then_pause(*args: object, **kwargs: object) -> None:
        audit(*args, **kwargs)
        _policy(root, True)

    monkeypatch.setattr(queue, "_audit", audit_then_pause)
    with TestClient(app) as api:
        response = api.post("/tasks", json={
            "title": "atomic acquire", "repo": TEST_REPO, "claim_as": "host/wt",
        })
    assert response.status_code == 409
    assert queue.list(repo=TEST_REPO) == []


@pytest.mark.parametrize("verdict,complete", [
    ("gone", True), ("gone", False), ("live", True), ("unknown", True),
])
def test_exited_unclaimed_script_preserves_task_and_inhibition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: str, complete: bool,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, False)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    queue = app.state.queue
    task = queue.create("script work", repo=TEST_REPO)
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="lane")
    task_file = tmp_path / "script-task.json"
    task_file.write_text("{}")
    queue.record_spawn(
        reservation.key,
        session_handle="script-body:" + json.dumps({
            "worker_id": "script-one", "pid": 4321, "start_token": "token",
            "task_file": str(task_file),
        }),
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    if not complete:
        queue.record_spawn(
            reservation.key, startup_complete=False,
            startup_revision=queue.get_reservation(reservation.key).startup_revision,
        )
    pending = queue.get_reservation(reservation.key)
    lane = Supervisor(
        QueueBackedClient(queue), spawn_fn=_ok_spawn(), repo=TEST_REPO,
        script_body_verdict_fn=lambda *_: verdict,
    )
    _policy(root, True)
    assert lane.recover_gone() == 0
    assert queue.get_reservation(reservation.key) == pending
    assert task_file.exists()
    _policy(root, False)
    assert lane.recover_gone() == (1 if verdict == "gone" and complete else 0)
    assert queue.get(task.id).status == "queued"
    assert queue.get(task.id).attempts == 0
    if verdict == "gone" and complete:
        assert not task_file.exists()
        retry, reserved = queue.reserve_spawn(task.id, reserved_by="lane")
        assert reserved and retry.creation_key == pending.creation_key
    else:
        assert queue.get_reservation(reservation.key) == pending


@pytest.mark.parametrize("handled", [False, True])
def test_canceled_admission_wait_does_not_report_terminal_failure(
    monkeypatch: pytest.MonkeyPatch, handled: bool,
) -> None:
    from agent_dispatch import script_worker

    client = FakeClient()

    def deny(**_kwargs: object) -> None:
        raise DispatchError(409, {
            "code": "action_inhibited", "action": "worker-admission", "retryable": True,
        })

    def cancel(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(client, "claim", deny)
    monkeypatch.setattr(script_worker.time, "sleep", cancel)

    def run() -> None:
        with ScriptTaskRuntime(client, "pending", "worker") as runtime:
            try:
                runtime.announce_start()
            except KeyboardInterrupt:
                if not handled:
                    raise

    if handled:
        run()
    else:
        with pytest.raises(KeyboardInterrupt):
            run()
    assert not client.abandon_calls and client.closed


def test_independent_hold_after_admission_wait_preserves_unclaimed_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    queue = app.state.queue
    task = queue.create("held script work", repo=TEST_REPO)
    denied = threading.Event()
    errors: list[BaseException] = []
    url, stop = _boot(app)

    class ObservedClient(DispatchClient):
        def claim(self, **kwargs: object) -> dict | None:
            try:
                return super().claim(**kwargs)
            except DispatchError:
                denied.set()
                raise

    runtime = ScriptTaskRuntime(
        ObservedClient(url), task.id, "script-one", repo=TEST_REPO,
        heartbeat_seconds=0, admission_retry_seconds=0.01,
    )

    def run() -> None:
        try:
            with runtime as worker:
                worker.announce_start()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert denied.wait(5)
        held = queue.set_hold(task.id, reason="independent pause", actor="operator")
        _policy(root, False)
        thread.join(5)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
        assert "claim did not return task" in str(errors[0])
        assert queue.get(task.id) == held
        assert queue.get(task.id).status == "queued"
        assert queue.get(task.id).attempts == 0
    finally:
        _policy(root, False)
        thread.join(5)
        stop()


@pytest.mark.parametrize("claim", [None, {}, {"id": "other-task"}, []])
def test_invalid_claim_after_admission_wait_does_not_abandon(
    monkeypatch: pytest.MonkeyPatch, claim: object,
) -> None:
    from agent_dispatch import script_worker

    client = FakeClient()
    attempts = 0

    def deny_then_return(**_kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DispatchError(409, {
                "code": "action_inhibited", "action": "worker-admission", "retryable": True,
            })
        return claim

    monkeypatch.setattr(client, "claim", deny_then_return)
    monkeypatch.setattr(script_worker.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="claim did not return task"):
        with ScriptTaskRuntime(client, "pending", "worker") as runtime:
            runtime.announce_start()
    assert attempts == 2
    assert not client.start_calls and not client.abandon_calls and client.closed


@pytest.mark.parametrize("detail", [
    "worker-admission is inhibited",
    {"code": "other", "action": "worker-admission", "retryable": True},
    {"code": "action_inhibited", "action": "other", "retryable": True},
    {"code": "action_inhibited", "action": "worker-admission", "retryable": "true"},
])
def test_unqualified_error_is_not_an_automatic_admission_retry(detail: object) -> None:
    assert not DispatchError(409, detail).is_action_inhibited("worker-admission")
