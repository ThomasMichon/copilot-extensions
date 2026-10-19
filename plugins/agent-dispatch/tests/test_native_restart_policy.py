"""The opt-in task snapshot cannot start infrastructure or grant a launch."""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from agent_procutil import no_window_kwargs

from agent_dispatch import __main__ as core


NATIVE = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def task():
    return {
        "id": "task-1", "repo": "project", "status": "started", "owner": "machine/worktree",
        "owner_session_id": NATIVE, "generation": 7, "awaiting_steer": False,
        "resume_requested": False, "hold_reason": None, "hold_actor": None, "hold_at": None,
        "run_waiter": None, "pending_steer": False,
    }


def invoke(monkeypatch, capsys, task):
    seen = {}

    @contextlib.contextmanager
    def client(args, **kwargs):
        seen.update(kwargs)

        class Client:
            def get(self, task_id, **kwargs):
                assert task_id == "task-1"
                assert kwargs == {"native_restart_policy": True}
                from agent_dispatch.native_restart_policy import snapshot
                return {"exists": True, "native_restart_policy": snapshot(task, task_id=task_id)}

        yield Client()

    monkeypatch.setattr(core, "_client", client)
    args = argparse.Namespace(task_id="task-1", local=True, native_restart_policy=True)
    assert core._cmd_claim_status(args) == 0
    assert seen == {"ensure": False, "allow_ssh_failover": False, "read_only": True}
    return json.loads(capsys.readouterr().out)


def test_explicit_policy_snapshot_is_bounded_and_not_launch_permission(monkeypatch, capsys, task):
    task["prompt"] = "private task prompt is not part of the callback"
    result = invoke(monkeypatch, capsys, task)
    policy = result["native_restart_policy"]
    assert result["exists"] is True
    assert policy == {
        "schema": "native-restart-task-policy", "version": 1,
        "task_id": "task-1", "repo": "project", "state": "started",
        "owner": "machine/worktree", "owner_session_id": NATIVE, "generation": 7,
        "awaiting_steer": False, "pending_steer": False, "resume_requested": False,
        "hold_reason": None, "hold_actor": None, "hold_at": None, "run_waiter": False,
        "task_qualified": True, "launch_authorized": False, "automatic_eligible": False,
        "reason": "current-task-matches",
    }
    assert "prompt" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"status": status} for status in
    ("proposed", "queued", "claimed", "suspended", "submitted", "completed", "abandoned")
] + [
    {"owner": None}, {"owner": "opaque"}, {"owner_session_id": None},
    {"owner_session_id": "opaque"}, {"generation": 0}, {"generation": True},
    {"generation": 2**64}, {"awaiting_steer": True}, {"awaiting_steer": None},
    {"resume_requested": True}, {"hold_reason": "operator pause"},
    {"hold_actor": "operator"}, {"hold_at": 1}, {"run_waiter": {"command": ["wait"]}},
    {"id": "different-task"}, {"hold_at": float("inf")}, {"hold_at": float("nan")},
    {"hold_at": 10**400}, {"hold_at": True},
    {"repo": "x" * 4097}, {"repo": "\u2603" * 4096},
    {"pending_steer": True}, {"pending_steer": 0},
])
def test_unqualified_current_task_never_grants_launch(monkeypatch, capsys, task, change):
    result = invoke(monkeypatch, capsys, {**task, **change})
    assert result["native_restart_policy"]["task_qualified"] is False
    assert result["native_restart_policy"]["launch_authorized"] is False


@pytest.mark.parametrize("field", [
    "owner_session_id", "generation", "awaiting_steer", "resume_requested",
    "hold_reason", "hold_actor", "hold_at", "run_waiter", "repo", "pending_steer",
])
def test_missing_fields_are_not_assumed_safe(monkeypatch, capsys, task, field):
    del task[field]
    assert invoke(monkeypatch, capsys, task)["native_restart_policy"]["task_qualified"] is False


def test_opt_in_requires_explicit_authority_before_client(monkeypatch):
    monkeypatch.setattr(core, "_client", lambda *a, **kw: pytest.fail("ambiguous authority read"))
    args = argparse.Namespace(task_id="task-1", native_restart_policy=True)
    from agent_dispatch.client import DispatchError
    with pytest.raises(DispatchError, match="explicit"):
        core._cmd_claim_status(args)


def test_readonly_client_never_ensures_or_even_probes_ssh_failover(monkeypatch):
    from agent_dispatch import task_worker_cli

    monkeypatch.setattr(task_worker_cli, "task_worker", lambda args: None)
    monkeypatch.setattr(core, "_ensure_local_coordinator", lambda args: pytest.fail("autostart"))
    monkeypatch.setattr(core, "_should_ssh_failover", lambda args: pytest.fail("SSH failover"))
    monkeypatch.setattr(core, "_resolve_client_target", lambda args, **kw: ("http://127.0.0.1:1", None))
    monkeypatch.setattr(core, "client_control_token", lambda: pytest.fail("control credential fetch"))
    with core._client(
        argparse.Namespace(local=True), ensure=False, allow_ssh_failover=False, read_only=True,
    ):
        pass


def test_parser_exposes_opt_in():
    args = core.build_parser().parse_args([
        "--local", "claim-status", "task-1", "--native-restart-policy",
    ])
    assert args.native_restart_policy is True


def test_readonly_shared_target_uses_existing_bearer_not_credential_commands(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "fixture-existing")
    monkeypatch.setattr(core, "shared_url", lambda: "https://coordinator.invalid")
    monkeypatch.setattr(core, "shared_token", lambda: pytest.fail("credential command"))
    args = argparse.Namespace(shared=True)
    assert core._resolve_client_target(args, read_only=True) == (
        "https://coordinator.invalid", "fixture-existing",
    )


def test_readonly_target_never_uses_legacy_reachability_fallback(monkeypatch):
    from agent_dispatch.client import DispatchError

    monkeypatch.delenv("AGENT_DISPATCH_TASK_AUTHORITY", raising=False)
    monkeypatch.setattr(core, "has_live_local_coordinator", lambda: pytest.fail("fallback probe"))
    with pytest.raises(DispatchError, match="explicit"):
        core._resolve_client_target(argparse.Namespace(), read_only=True)


@pytest.fixture
def owner_api(tmp_path):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.queue import TaskQueue

    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create(title="task", prompt="not a policy field", repo="project")
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET status='started', owner=?, owner_session_id=?, generation=7 WHERE id=?",
            ("machine/worktree", NATIVE, task.id),
        )
    # Route-only requests do not need resident lifecycle/background loops.
    client = TestClient(create_app(queue))
    try:
        yield client, queue, task.id
    finally:
        client.close()


def test_actual_owner_route_is_opt_in_reduced_and_read_only(owner_api):
    client, queue, task_id = owner_api
    before = queue.get(task_id)
    ordinary = client.get(f"/tasks/{task_id}")
    assert ordinary.status_code == 200
    assert "run_waiter" not in ordinary.json()
    assert ordinary.json()["prompt"] == "not a policy field"
    response = client.get(f"/tasks/{task_id}?native_restart_policy=true")
    assert response.status_code == 200
    assert set(response.json()) == {"exists", "native_restart_policy"}
    policy = response.json()["native_restart_policy"]
    assert policy["task_qualified"] is True
    assert policy["owner_session_id"] == NATIVE
    assert policy["run_waiter"] is False
    assert "prompt" not in json.dumps(response.json())
    assert queue.get(task_id) == before
    assert client.get("/tasks/absent?native_restart_policy=true").status_code == 404


def test_actual_owner_hold_is_not_replaced_by_historical_provenance(owner_api):
    client, queue, task_id = owner_api
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET hold_reason='operator hold' WHERE id=?", (task_id,))
    policy = client.get(f"/tasks/{task_id}?native_restart_policy=true").json()["native_restart_policy"]
    assert policy["task_qualified"] is False
    assert policy["hold_reason"] == "operator hold"


def test_unconsumed_operator_steering_blocks_until_the_inbox_is_empty(owner_api):
    client, queue, task_id = owner_api

    def current():
        return client.get(
            f"/tasks/{task_id}?native_restart_policy=true",
        ).json()["native_restart_policy"]

    assert current()["task_qualified"] is True
    for index in range(2):
        queue.submit_steer(task_id, fields={"direction": f"check status {index}"})
    task = queue.get(task_id)
    assert task.status == "started"
    assert task.awaiting_steer is False
    assert task.resume_requested is False
    pending = current()
    assert pending["task_qualified"] is False
    assert pending["pending_steer"] is True
    assert "direction" not in json.dumps(pending)
    queue.take_steer(task_id, "machine/worktree")
    assert current()["task_qualified"] is False
    queue.take_steer(task_id, "machine/worktree", all_pending=True)
    consumed = current()
    assert consumed["pending_steer"] is False
    assert consumed["task_qualified"] is True
    assert consumed["launch_authorized"] is False


@pytest.mark.parametrize("state", ["active", "preparing"])
def test_actual_owner_rejects_active_and_preparing_waiters(owner_api, state):
    client, queue, task_id = owner_api
    # Register through the existing owner API, then cover both waiter states.
    response = client.post(
        f"/tasks/{task_id}/run-waiter/register",
        json={
            "worker_id": "machine/worktree", "reason": "waiting", "host": "host",
            "resume_worktree": "worktree", "command": ["wait"],
        },
    )
    assert response.status_code == 200, response.text
    with queue._connect() as conn:
        conn.execute("UPDATE run_waiters SET state=? WHERE task_id=?", (state, task_id))
        # Even if task status races back to started, the waiter still blocks.
        conn.execute("UPDATE tasks SET status='started' WHERE id=?", (task_id,))
    policy = client.get(f"/tasks/{task_id}?native_restart_policy=true").json()["native_restart_policy"]
    assert policy["task_qualified"] is False
    assert policy["run_waiter"] is True


def test_old_coordinator_cannot_satisfy_opt_in_by_ignoring_query(monkeypatch, capsys):
    @contextlib.contextmanager
    def client(*args, **kwargs):
        class OldClient:
            def get(self, *args, **kwargs):
                return {"id": "task-1", "status": "started", "owner": "machine/worktree"}
        yield OldClient()

    monkeypatch.setattr(core, "_client", client)
    assert core._cmd_claim_status(argparse.Namespace(
        task_id="task-1", local=True, native_restart_policy=True,
    )) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not confirm native restart policy" in captured.err


def test_actual_child_reads_only_current_task_over_http(task):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from agent_dispatch.native_restart_policy import snapshot

            requests.append(("GET", self.path))
            raw = json.dumps({
                "exists": True, "native_restart_policy": snapshot(task, task_id="task-1"),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    code = """
import sys
from agent_dispatch import __main__ as core
core._resolve_client_target = lambda args, **kwargs: (sys.argv[1], None)
def forbidden(*args, **kwargs):
    raise AssertionError("read-only callback attempted infrastructure or credential work")
core._ensure_local_coordinator = forbidden
core._should_ssh_failover = forbidden
core.client_control_token = forbidden
raise SystemExit(core.main(["--local", "claim-status", "task-1", "--native-restart-policy"]))
"""
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            process = subprocess.run(
                [sys.executable, "-c", code, f"http://127.0.0.1:{server.server_port}"],
                capture_output=True, text=True, timeout=20, check=False, **no_window_kwargs(),
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
        assert not thread.is_alive()
    assert process.returncode == 0, process.stderr
    assert requests == [("GET", "/tasks/task-1?native_restart_policy=true")]
    result = json.loads(process.stdout)["native_restart_policy"]
    assert result["generation"] == task["generation"]
    assert result["owner_session_id"] == NATIVE
    assert result["task_qualified"] is True
    assert result["launch_authorized"] is False
