"""Internal owner reads retain proposal, task claim and native capacity."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from agent_dispatch import submission_readiness_observation as observation
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.procutil import run_background_capture as capture_worktrees
from agent_dispatch.queue import Status, TaskError, TaskQueue
from tests import test_submission_preparation as preparation_controls
from tests.test_assignment_relationships import GroundOwner
from tests.test_pool_placement import place, pool, publish

submission = preparation_controls.submission


@pytest.fixture
def ready(tmp_path, monkeypatch):
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.db", relationship_writer=owner, relationship_authority="local"
    )
    owner.queue = queue
    workspace = AssignmentWorkspace("host-a", "example", "tree", str(tmp_path / "tree"), "created")
    publish(queue, pool())
    task = queue.create("work", repo="example", require_verification=True, evaluator_ref="review")
    reservation, _ = place(queue, task)
    queue.record_spawn_worktree(
        reservation.key,
        workspace.worktree,
        ownership="created",
        creating_host=workspace.machine,
        execution_machine=workspace.machine,
        driver="cli",
        allocation_project=workspace.project,
        worktree_path=workspace.path,
        creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(
        reservation.key, session_handle="native", startup_complete=True, startup_revision=1
    )
    task = queue.start(
        task.id,
        workspace.worker_id,
        owner_session_id="native",
        expected_generation=queue.get(task.id).generation,
    )
    proposal = queue.prepare_submission(
        task.id,
        workspace.worker_id,
        request_id="proposal",
        expected_generation=task.generation,
        expected_owner_session_id="native",
        workspace=workspace,
        result={"answer": "done"},
    )
    allocation = {
        "task_id": task.id,
        "reservation_key": reservation.key,
        "attempt": reservation.attempt,
        "driver": "cli",
        "supervisor": "allocator",
        "creator_machine": workspace.machine,
        "ownership": "created",
    }
    descriptor = {
        "contract": observation.CONTRACT,
        "version": 1,
        "operations": ["observe-ready", "check-own-ready"],
        "platforms": ["linux", "wsl"],
        "native_proof": "inuse-hold-v1",
        "paired_readiness": False,
        "finalized": False,
        "task_release": False,
        "process_retirement": False,
    }
    metadata = {
        "ok": True,
        "context": {
            "schema": "agent-worktrees.workspace-context",
            "version": 1,
            "project": workspace.project,
            "root": workspace.path,
            "worktree": {
                "id": workspace.worktree,
                "repo": workspace.project,
                "path": workspace.path,
                "creation_nonce": workspace.creation_nonce,
                "status": "active",
                "dispatch_attempt_opaque": False,
                "checkout_managed": True,
                "dispatch_attempt": allocation,
                "head": {
                    "version": 1,
                    "worktree_id": workspace.worktree,
                    "tracked": True,
                    "head_session": "native",
                    "active": True,
                    "state": "active",
                    "head_revision": 7,
                    "pending_handoffs": [],
                },
            },
        },
    }
    value = {
        "ok": True,
        "contract": observation.CONTRACT,
        "version": 1,
        "operation": "observe-ready",
        "ready": True,
        "task_id": task.id,
        "reservation_key": reservation.key,
        "native_id": "native",
        "worktree_id": workspace.worktree,
        "creation_nonce": workspace.creation_nonce,
        "head_revision": 7,
        "task_claim_ref": queue.task_relationship(task.id)["receipt"]["ref"],
        "project": workspace.project,
        "path": workspace.path,
        "machine": "HOST-A",
        "allocation": allocation,
        "checkout": {"head": "a" * 40, "branch": "worktree/worker"},
        "landing": {"bases": ["origin/dev"], "prs": []},
        "checkout_retained": True,
        "finalized": False,
        "task_release": False,
        "process_retirement": False,
    }
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "--describe-readiness-contract" in argv:
            response = descriptor
        elif "--cwd" in argv:
            response = metadata
        else:
            response = value
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    monkeypatch.setattr(observation.remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(
        observation.procutil, "agent_worktrees_launch_prefix", lambda: ["selected-worktrees"]
    )
    monkeypatch.setattr(observation.procutil, "run_background_capture", run)
    return queue, owner, task, proposal, descriptor, metadata, value, calls


def retained(queue, task):
    return (
        queue.get(task.id),
        queue.submission_preparation(task.id),
        queue.task_relationship(task.id),
        queue.list_reservations(task_id=task.id),
        queue.task_pool_assignment(task.id),
        queue.events(task.id),
    )


def test_negotiates_once_pins_actual_owner_head_and_never_submits_or_finalizes(ready, monkeypatch):
    queue, owner, task, proposal, _, _, value, calls = ready
    resolved = []

    def prefix():
        resolved.append(True)
        return ["selected-worktrees"]

    monkeypatch.setattr(observation.procutil, "agent_worktrees_launch_prefix", prefix)
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/foreign/payload")
    monkeypatch.setenv("PYTHONPATH", "/foreign/python")
    before = retained(queue, task)
    result = queue.observe_submission_readiness(task.id, request_id="proposal")
    assert result == {"preparation": proposal, "observation": value}
    assert retained(queue, task) == before and len(owner.claims) == 1
    assert resolved == [True] and len(calls) == 3
    assert calls[0][0][-2:] == ["--describe-readiness-contract", "--json"]
    assert calls[1][0][-3:] == ["--cwd", proposal["request"]["workspace"]["path"], "--json"]
    argv = calls[2][0]
    assert argv[argv.index("--readiness-head-revision") + 1] == "7"
    assert argv[argv.index("--readiness-task-claim-ref") + 1] == value["task_claim_ref"]
    assert argv[argv.index("--readiness-operation") + 1] == "observe-ready"
    assert all(call[1]["timeout"] == 15 for call in calls)
    assert all(call[1]["env"] is calls[0][1]["env"] for call in calls)
    assert all(
        "COPILOT_PLUGIN_ROOT" not in call[1]["env"] and "PYTHONPATH" not in call[1]["env"]
        for call in calls
    )
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM verification_requests").fetchone()[0] == 0
    with pytest.raises(TaskError, match="authoritative readiness"):
        queue.complete(task.id, task.owner)
    with (
        queue._connect() as conn,
        pytest.raises(sqlite3.IntegrityError, match="authoritative readiness"),
    ):
        conn.execute("UPDATE tasks SET status='submitted' WHERE id=?", (task.id,))
    assert queue.get(task.id).status == Status.STARTED
    queue.observe_submission_readiness(task.id, request_id="proposal")
    assert len(calls) == 6 and retained(queue, task) == before


@pytest.mark.parametrize(
    "change",
    [
        {"contract": "agent-worktrees.dispatch-worker-closeout"},
        {"version": True},
        {"version": 2},
        {"operations": ["check-own-ready"]},
        {"platforms": ["win"]},
        {"native_proof": "asserted"},
        {"paired_readiness": True},
        {"finalized": True},
        {"task_release": True},
        {"process_retirement": True},
    ],
)
def test_prior_or_incompatible_runtime_stops_at_discovery_without_fallback(ready, change):
    queue, _, task, _, descriptor, _, _, calls = ready
    descriptor.update(change)
    before = retained(queue, task)
    with pytest.raises(TaskError, match="readiness v1"):
        queue.observe_submission_readiness(task.id, request_id="proposal")
    assert len(calls) == 1 and retained(queue, task) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("creation_nonce", "replacement"),
        ("path", "/foreign"),
        ("status", "finalized"),
        ("dispatch_attempt_opaque", True),
        ("dispatch_attempt", {}),
        ("head", {"head_revision": 7}),
        ("checkout_managed", False),
        ("checkout_managed", 1),
        ("checkout_managed", "true"),
    ],
)
def test_actual_record_mismatch_never_reaches_readiness(ready, field, value):
    queue, _, task, _, _, metadata, _, calls = ready
    metadata["context"]["worktree"][field] = value
    with pytest.raises(TaskError, match="workspace or native head"):
        queue.observe_submission_readiness(task.id, request_id="proposal")
    assert len(calls) == 2 and queue.get(task.id) == task


@pytest.mark.parametrize(
    "change",
    [
        {"ok": 1},
        {"version": True},
        {"operation": "check-own-ready"},
        {"ready": False},
        {"task_id": "foreign"},
        {"reservation_key": "foreign"},
        {"native_id": "foreign"},
        {"worktree_id": "foreign"},
        {"creation_nonce": "foreign"},
        {"head_revision": True},
        {"head_revision": 8},
        {"task_claim_ref": "copied"},
        {"machine": "host-b"},
        {"path": "/foreign"},
        {"allocation": {}},
        {"checkout": {}},
        {"landing": {}},
        {"checkout_retained": False},
        {"finalized": True},
        {"task_release": 0},
        {"process_retirement": True},
    ],
)
def test_unqualified_success_shaped_observation_retains_all_custody(ready, change):
    queue, _, task, _, _, _, value, calls = ready
    value.update(change)
    before = retained(queue, task)
    with pytest.raises(TaskError, match="outcome changed"):
        queue.observe_submission_readiness(task.id, request_id="proposal")
    assert len(calls) == 3 and retained(queue, task) == before


@pytest.mark.parametrize("stage", [1, 2, 3])
@pytest.mark.parametrize("failure", ["timeout", "malformed", "refused", "missing"])
def test_boundary_failure_is_not_replayed_or_downgraded(ready, monkeypatch, stage, failure):
    queue, _, task, _, _, _, _, calls = ready
    run = observation.procutil.run_background_capture

    def failing(argv, **kwargs):
        if len(calls) + 1 == stage:
            calls.append((argv, kwargs))
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 15)
            if failure == "missing":
                return None
            return subprocess.CompletedProcess(
                argv,
                1 if failure == "refused" else 0,
                '{"ok":false,"error":"worktree retains WIP"}' if failure == "refused" else "{",
                "",
            )
        return run(argv, **kwargs)

    monkeypatch.setattr(observation.procutil, "run_background_capture", failing)
    before = retained(queue, task)
    with pytest.raises(TaskError, match="custody retained"):
        queue.observe_submission_readiness(task.id, request_id="proposal")
    assert len(calls) == stage and retained(queue, task) == before


@pytest.mark.parametrize("host", [None, "host-b"])
def test_remote_observation_never_launches_a_local_owner(ready, monkeypatch, host):
    queue, _, task, _, _, _, _, calls = ready
    monkeypatch.setattr(observation.remote_dispatch, "local_machine", lambda: host)
    monkeypatch.setattr(
        observation.procutil,
        "agent_worktrees_launch_prefix",
        lambda: pytest.fail("Do not resolve a foreign runtime"),
    )
    with pytest.raises(TaskError, match="qualified local"):
        queue.observe_submission_readiness(task.id, request_id="proposal")
    assert not calls and queue.get(task.id) == task


@pytest.mark.parametrize("change", ["hold", "steer", "native", "blocker", "result"])
def test_queue_recheck_after_external_observation_does_not_hold_a_write_lock(
    ready, monkeypatch, change
):
    queue, _, task, _, _, _, value, calls = ready
    run = observation.procutil.run_background_capture

    def changed(argv, **kwargs):
        response = run(argv, **kwargs)
        if "--readiness-operation" in argv:
            if change == "hold":
                queue.set_hold(task.id, actor="operator", reason="inspection")
            elif change == "blocker":
                queue.record_submission_blocker(
                    task.id,
                    request_id="proposal",
                    code="changed",
                    message="Not ready",
                )
            elif change == "result":
                with queue._connect() as conn:
                    conn.execute("UPDATE tasks SET result_ref='changed' WHERE id=?", (task.id,))
            else:
                with queue._connect() as conn:
                    statement = {
                        "steer": "UPDATE tasks SET awaiting_steer=1 WHERE id=?",
                        "native": "UPDATE tasks SET owner_session_id='replacement' WHERE id=?",
                    }[change]
                    conn.execute(statement, (task.id,))
        return response

    monkeypatch.setattr(observation.procutil, "run_background_capture", changed)
    with pytest.raises(TaskError):
        queue.observe_submission_readiness(task.id, request_id="proposal")
    assert len(calls) == 3 and value["task_release"] is False
    assert queue.get(task.id).status == Status.STARTED


def test_manual_workspace_without_managed_allocation_remains_explicitly_unsupported(
    submission, monkeypatch
):
    queue, _, task, arguments = submission
    queue.prepare_submission(task.id, task.owner, **arguments)
    monkeypatch.setattr(
        observation.procutil,
        "agent_worktrees_launch_prefix",
        lambda: pytest.fail("No allocation authority"),
    )
    with pytest.raises(TaskError, match="qualified managed allocation"):
        queue.observe_submission_readiness(task.id, request_id=arguments["request_id"])
    assert queue.get(task.id) == task


def test_changed_bound_receipt_ref_refuses_before_worktrees(ready):
    queue, _, task, _, _, _, _, calls = ready
    relationship = deepcopy(queue.task_relationship(task.id))
    relationship["receipt"]["ref"] = "copied"
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_events SET task_relationship=? WHERE id=?",
            (json.dumps(relationship), relationship["request"]["event_id"]),
        )
    with pytest.raises(TaskError, match="original bound task claim"):
        queue.observe_submission_readiness(task.id, request_id="proposal")
    assert not calls and queue.get(task.id) == task


@pytest.mark.parametrize("progress", [None, False, True])
def test_unrelated_heartbeat_and_progress_during_observation_preserve_the_proposal(
    ready,
    monkeypatch,
    progress,
):
    queue, owner, task, proposal, _, _, value, _ = ready
    run = observation.procutil.run_background_capture
    relationship = queue.task_relationship(task.id)
    allocation = queue.list_reservations(task_id=task.id)

    def beat(argv, **kwargs):
        response = run(argv, **kwargs)
        if "--readiness-operation" in argv:
            if progress is None:
                queue.heartbeat(task.id, task.owner, now=task.updated_at + 1)
            else:
                queue.record_progress(
                    task.id,
                    task.owner,
                    phase="review",
                    summary="Waiting for a verdict",
                    extend_lease=progress,
                    now=task.updated_at + 1,
                )
        return response

    monkeypatch.setattr(observation.procutil, "run_background_capture", beat)
    assert queue.observe_submission_readiness(task.id, request_id="proposal") == {
        "preparation": proposal,
        "observation": value,
    }
    fresh = queue.get(task.id)
    assert fresh.updated_at != task.updated_at and fresh.status == task.status
    assert (fresh.owner, fresh.owner_session_id, fresh.generation) == (
        task.owner,
        task.owner_session_id,
        task.generation,
    )
    assert queue.submission_preparation(task.id) == proposal
    assert queue.task_relationship(task.id) == relationship
    assert queue.list_reservations(task_id=task.id) == allocation and len(owner.claims) == 1


@pytest.mark.parametrize("provider", ["current", "prior", "rollback"])
def test_selected_process_uses_actual_record_parser_and_refuses_prior_or_rollback(
    ready,
    monkeypatch,
    tmp_path,
    provider,
):
    queue, _, task, _, _, metadata, value, _ = ready
    script = tmp_path / "selected.py"
    replies = tmp_path / "replies.json"
    invocations = tmp_path / "invocations.jsonl"
    root = Path(__file__).resolve().parents[3]
    replies.write_text(json.dumps({"metadata": metadata, "observation": value}), encoding="utf-8")
    script.write_text(
        """import argparse, json, sys
from pathlib import Path
root, replies, log, mode = sys.argv[1:5]
args = sys.argv[5:]
path = Path(log)
previous = path.read_text().splitlines() if path.exists() else []
with path.open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
old = mode == "prior" or mode == "rollback" and bool(previous)
if old:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("--cwd", required=True)
    record.add_argument("--json", action="store_true")
    parser.parse_args(args)
    print(json.dumps(json.loads(Path(replies).read_text())["metadata"]))
else:
    root = Path(root)
    sys.path[:0] = [str(root / "plugins/agent-worktrees/src")]
    sys.path[:0] = [str(p / "src") for p in (root / "libs").iterdir() if (p / "src").is_dir()]
    from agent_worktrees import record_cli, record_read, dispatch_worker_readiness, tracking
    payload = json.loads(Path(replies).read_text())
    tree = payload["metadata"]["context"]["worktree"]
    record_path = Path(replies).with_name(tree["id"] + ".yaml")
    data = {
        "worktree_id": tree["id"], "repo": tree["repo"], "worktree_path": tree["path"],
        "branch": "worktree/worker", "status": "active", "machine": "host-a", "platform": "linux",
        "creation_nonce": tree["creation_nonce"], "dispatch_attempt": tree["dispatch_attempt"],
        "sessions": [{"session_id": "native", "state": "active"}],
        "head_transitions": [{"revision": 7, "session_id": "native", "reason": "registered"}],
    }
    record = tracking.parse_record_data(data, path=record_path)
    tracking._save_record_contents_unlocked(record, record_path, current=None)
    assert "checkout_managed" not in record_path.read_text()
    record_read.cfg.project_name = lambda: tree["repo"]
    record_read.configured_repositories = lambda project: {tree["repo"]}
    record_read.cfg.tracking_dir = lambda project: record_path.parent
    payload["metadata"]["context"]["worktree"] = record_read.read_exact_record(
        tree["id"], include_workspace=True,
    )
    assert payload["metadata"]["context"]["worktree"]["checkout_managed"] is None
    record_cli.read_workspace_record = lambda cwd: payload["metadata"]["context"]
    def observe(worktree, task, reservation, native, **kwargs):
        value = payload["observation"]
        assert [worktree, task, reservation, native] == [
            value[k] for k in ("worktree_id", "task_id", "reservation_key", "native_id")
        ]
        assert kwargs == {
            "creation_nonce": value["creation_nonce"], "head_revision": value["head_revision"],
            "task_claim_ref": value["task_claim_ref"], "own_caller": False,
        }
        return value
    dispatch_worker_readiness.observe_ready = observe
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    record_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    sys.exit(record_cli.cmd_record(parser.parse_args(args)))
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(observation.procutil, "run_background_capture", capture_worktrees)
    monkeypatch.setattr(
        observation.procutil,
        "agent_worktrees_launch_prefix",
        lambda: [
            sys.executable,
            "-I",
            str(script),
            str(root),
            str(replies),
            str(invocations),
            provider,
        ],
    )
    before = retained(queue, task)
    if provider == "current":
        assert (
            queue.observe_submission_readiness(
                task.id,
                request_id="proposal",
            )["observation"]
            == value
        )
    else:
        with pytest.raises(TaskError, match="custody retained"):
            queue.observe_submission_readiness(task.id, request_id="proposal")
    assert retained(queue, task) == before
    calls = [json.loads(line) for line in invocations.read_text().splitlines()]
    assert len(calls) == (1 if provider == "prior" else 3)
    assert all("finalize" not in argv and "--closeout-task" not in argv for argv in calls)
