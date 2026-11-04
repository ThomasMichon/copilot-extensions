"""Task responsibility is independent of runner lifetime and provisional results."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as core
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from agent_dispatch.task_obligations import read_snapshot, valid_snapshot


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def claimed(q, *, require_verification=False):
    task = q.create(
        "work", prompt="private prompt", repo="project", require_verification=require_verification,
    )
    q.claim_one("machine/worktree", task_id=task.id)
    return task.id


def test_submission_and_runner_lease_expiry_do_not_settle(q):
    task_id = claimed(q, require_verification=True)
    q.start(task_id, "machine/worktree", owner_session_id="native")
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET lease_expires_at=0 WHERE id=?", (task_id,))
    assert read_snapshot(q, task_id)["release_outcome"] is None
    q.complete(task_id, "machine/worktree", result={"answer": "provisional"})
    submitted = read_snapshot(q, task_id)
    assert submitted["state"] == "submitted" and not submitted["bindings"]["owner"]
    assert submitted["release_outcome"] is None
    q.confirm(task_id)
    assert read_snapshot(q, task_id)["release_outcome"] == "completed"
    assert read_snapshot(TaskQueue(q.db_path), task_id) == read_snapshot(q, task_id)


def test_existing_self_attested_completion_is_not_reclassified(q):
    task_id = claimed(q)
    q.start(task_id, "machine/worktree")
    q.complete(task_id, "machine/worktree")
    assert read_snapshot(q, task_id)["release_outcome"] == "completed"


def test_only_confirmed_unassignment_to_unbound_queue_is_a_release_outcome(q):
    task_id = claimed(q)
    q.yield_task(task_id, "machine/worktree")
    value = read_snapshot(q, task_id)
    assert value["release_outcome"] == "unassigned_queued"
    assert value["unassignment"]["worker_id"] == "machine/worktree"
    assert valid_snapshot(value, task_id=task_id)
    # A same-state audit entry must not hide the actual disposition.
    q.yield_task(task_id, "machine/worktree")
    assert read_snapshot(q, task_id)["unassignment"] == value["unassignment"]
    q.claim_one("other/worktree", task_id=task_id)
    assert read_snapshot(q, task_id)["release_outcome"] is None


@pytest.mark.parametrize("operation", ["detach_agent", "reset_queued"])
def test_recovery_keeps_former_assignment_identity_separate_from_operator(q, operation):
    task_id = claimed(q)
    q.recover_task(
        task_id, operation=operation, actor="operator", reason="explicit recovery",
        expected_revision=q.task_diagnostics(task_id)["revision"], request_id="detach-1",
    )
    value = read_snapshot(q, task_id)
    assert value["release_outcome"] == "unassigned_queued"
    assert value["unassignment"]["worker_id"] == "machine/worktree"
    assert q.events(task_id)[-1]["worker"] == "operator"
    with q._connect() as conn:
        recorded = conn.execute(
            "SELECT unassigned_worker FROM task_events WHERE id=?", (value["unassignment"]["event_id"],),
        ).fetchone()
    assert recorded[0] == "machine/worktree"


def test_old_audit_writer_without_former_worker_does_not_invent_assignment_identity(q):
    task_id = claimed(q)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET status='queued', owner=NULL WHERE id=?", (task_id,))
        conn.execute(
            "INSERT INTO task_events(task_id, ts, from_status, to_status, worker, note)"
            " VALUES (?, 1, 'claimed', 'queued', 'operator', 'legacy detach')", (task_id,),
        )
    value = read_snapshot(q, task_id)
    assert value["unassignment"] is None and value["release_outcome"] is None


def test_task_event_migration_preserves_actor_and_does_not_backfill_former_worker(q):
    task_id = claimed(q)
    q.yield_task(task_id, "machine/worktree")
    with q._connect() as conn:
        conn.execute("ALTER TABLE task_events DROP COLUMN unassigned_worker")
    migrated = TaskQueue(q.db_path)
    value = read_snapshot(migrated, task_id)
    assert value["unassignment"] is None and value["release_outcome"] is None
    assert migrated.events(task_id)[-1]["worker"] == "machine/worktree"


@pytest.mark.parametrize("error", [None, "disk I/O error"])
def test_stale_migration_column_snapshot_only_tolerates_actual_duplicate_column(q, error):
    from agent_dispatch.queue_schema_migrations import run_queue_schema_migrations

    interleaved = False
    with q._connect() as conn:
        class InterleavedConnection:
            def execute(self, sql, *args):
                nonlocal interleaved
                if sql == "PRAGMA table_info(task_events)":
                    interleaved = True
                    # The other initializer added the column after this reader's snapshot.
                    return [row for row in conn.execute(sql) if row["name"] != "unassigned_worker"]
                if sql == "ALTER TABLE task_events ADD COLUMN unassigned_worker TEXT" and error:
                    raise sqlite3.OperationalError(error)
                return conn.execute(sql, *args)

        if error:
            with pytest.raises(sqlite3.OperationalError, match=error):
                run_queue_schema_migrations(InterleavedConnection(), now_fn=q._now)
        else:
            run_queue_schema_migrations(InterleavedConnection(), now_fn=q._now)
            assert sum(row["name"] == "unassigned_worker"
                       for row in conn.execute("PRAGMA table_info(task_events)")) == 1
    assert interleaved


def test_hard_worktree_requirement_survives_claim_and_yield(q):
    task = q.create("work", repo="project", requires=["worktree:workspace"])
    owner = "machine/workspace"
    assert q.claim_one(
        owner, machine="machine", worktree="workspace", task_id=task.id,
    ) is not None
    q.yield_task(task.id, owner)
    value = read_snapshot(q, task.id)
    assert value["bindings"]["worktree_requirement"]
    assert value["release_outcome"] is None
    assert q.claim_one(
        "other/elsewhere", machine="other", worktree="elsewhere", task_id=task.id,
    ) is None


@pytest.mark.parametrize("requires", ["null", "{}", "[1]", "invalid-json"])
def test_malformed_requirements_cannot_prove_no_worktree_requirement(q, requires):
    task_id = claimed(q)
    q.yield_task(task_id, "machine/worktree")
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET requires=? WHERE id=?", (requires, task_id))
    value = read_snapshot(q, task_id)
    assert value["bindings"]["worktree_requirement"] and value["release_outcome"] is None


@pytest.mark.parametrize("binding", ["owner", "owner_session_id", "target_worktree", "hold_reason"])
def test_queued_status_does_not_override_retained_binding_or_hold(q, binding):
    task_id = claimed(q)
    q.yield_task(task_id, "machine/worktree")
    with q._connect() as conn:
        conn.execute(f"UPDATE tasks SET {binding}='retained' WHERE id=?", (task_id,))
    assert read_snapshot(q, task_id)["release_outcome"] is None


def test_initial_queue_and_unaudited_status_change_are_not_unassignment(q):
    initial = q.create("work", repo="project")
    assert read_snapshot(q, initial.id)["release_outcome"] is None
    task_id = claimed(q)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET status='queued', owner=NULL WHERE id=?", (task_id,))
    assert read_snapshot(q, task_id)["release_outcome"] is None


@pytest.mark.parametrize("state", ["suspended", "abandoned", "dead_letter", "unknown"])
def test_other_task_states_never_imply_settlement(q, state):
    task_id = claimed(q)
    with q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, owner=NULL, owner_session_id=NULL WHERE id=?", (state, task_id),
        )
    assert read_snapshot(q, task_id)["release_outcome"] is None


def test_allocator_assignment_and_unreleased_spawn_bind_even_without_task_owner(q):
    from tests.test_pool_worker_decline import prepared_worker

    task_id, binding = prepared_worker(q)
    value = read_snapshot(q, task_id)
    assert not value["bindings"]["owner"]
    assert value["bindings"]["pool_assignment"] and value["bindings"]["spawn_reservation"]
    assert value["release_outcome"] is None
    # A bare task-row requeue cannot discharge allocator or spawn responsibility.
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (task_id,))
        q._audit(
            conn, task_id, ts=1, from_status="claimed", to_status="queued",
            worker=binding["worker_id"], note="test disposition",
            unassigned_worker=binding["worker_id"],
        )
    assert read_snapshot(q, task_id)["release_outcome"] is None


@pytest.mark.parametrize("state", ["unknown", "spawned", "releasing"])
def test_undisposed_or_unknown_spawn_binding_does_not_prove_unassignment(q, state):
    from tests.test_pool_worker_decline import prepared_worker

    task_id, binding = prepared_worker(q)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (task_id,))
        conn.execute("UPDATE task_pool_assignments SET assignment_released=1 WHERE task_id=?", (task_id,))
        conn.execute(
            "UPDATE spawn_reservations SET state=?, release_requested=0 WHERE task_id=?", (state, task_id),
        )
        q._audit(
            conn, task_id, ts=1, from_status="claimed", to_status="queued",
            worker=binding["worker_id"], note="test disposition",
            unassigned_worker=binding["worker_id"],
        )
    value = read_snapshot(q, task_id)
    assert value["bindings"]["spawn_reservation"] and value["release_outcome"] is None


def test_task_and_assignment_reads_share_one_sqlite_snapshot(q, monkeypatch):
    from tests.test_pool_worker_decline import prepared_worker

    task_id, _ = prepared_worker(q)
    connect = q._connect
    changed = False

    class Connection:
        def __init__(self):
            self.connection = connect()

        def execute(self, sql, *args):
            nonlocal changed
            cursor = self.connection.execute(sql, *args)
            if sql.startswith("SELECT id, status") and not changed:
                changed = True
                with connect() as writer:
                    writer.execute("UPDATE tasks SET status='queued' WHERE id=?", (task_id,))
                    writer.execute(
                        "UPDATE task_pool_assignments SET assignment_released=1 WHERE task_id=?", (task_id,),
                    )
            return cursor

        def rollback(self):
            self.connection.rollback()

        def close(self):
            self.connection.close()

    monkeypatch.setattr(q, "_connect", Connection)
    value = read_snapshot(q, task_id)
    assert changed and value["state"] == "claimed" and value["bindings"]["pool_assignment"]
    monkeypatch.setattr(q, "_connect", connect)
    assert read_snapshot(q, task_id)["state"] == "queued"
    assert not read_snapshot(q, task_id)["bindings"]["pool_assignment"]


def test_snapshot_uses_bounded_task_transition_lookup_without_mutation(q, monkeypatch):
    task_id = claimed(q)
    queries = []
    connect = q._connect

    def traced():
        conn = connect()
        conn.set_trace_callback(queries.append)
        return conn

    monkeypatch.setattr(q, "_connect", traced)
    read_snapshot(q, task_id)
    assert sum(query.startswith("SELECT") for query in queries) == 4
    assert not any(query.startswith(("UPDATE", "INSERT", "DELETE", "CREATE")) for query in queries)
    with connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id, from_status, to_status, worker FROM task_events"
            " WHERE task_id=? AND from_status IS NOT to_status ORDER BY id DESC LIMIT 1", (task_id,),
        ).fetchall()
    assert any("SEARCH task_events USING INDEX" in row[3] for row in plan)


@pytest.mark.parametrize("legacy", [False, True])
def test_actual_route_client_and_claim_callback_expand_without_new_syntax(q, monkeypatch, capsys, legacy):
    import json

    task_id = claimed(q)
    q.yield_task(task_id, "machine/worktree")
    before = asdict(q.get(task_id)), q.events(task_id)
    requests = []
    with TestClient(create_app(q)) as api:
        def handle(request):
            requests.append((request.method, request.url.path))
            response = api.get(request.url.path)
            body = response.json()
            if legacy:
                body.pop("task_obligation")
            return httpx.Response(response.status_code, json=body)

        with DispatchClient("http://coordinator.invalid", transport=httpx.MockTransport(handle)) as client:
            @contextmanager
            def selected(args, **options):
                assert args.local and options == {"ensure": False}
                yield client

            monkeypatch.setattr(core, "_client", selected)
            assert core.main(["--local", "claim-status", task_id]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["exists"] and result["state"] == "queued"
        if legacy:
            assert "task_obligation" not in result
        else:
            assert result["task_obligation"] == read_snapshot(q, task_id)
        assert "private prompt" not in json.dumps(result)
        assert api.get("/tasks/absent").status_code == 404
    assert requests == [("GET", f"/tasks/{task_id}")]
    assert before == (asdict(q.get(task_id)), q.events(task_id))


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"task_id": "foreign"}, {"generation": True},
    {"bindings": {}}, {"held": 0}, {"release_outcome": "completed"},
    {"unassignment": {"event_id": 1, "from_state": [], "worker_id": "worker"}},
])
def test_malformed_or_inconsistent_owner_evidence_is_not_a_release(q, change):
    task_id = claimed(q)
    value = read_snapshot(q, task_id)
    assert not valid_snapshot({**deepcopy(value), **change}, task_id=task_id)


def test_callback_refuses_malformed_expansion_without_claiming_absence(q, monkeypatch, capsys):
    task_id = claimed(q)

    class Client:
        def get(self, task_id):
            return {"id": task_id, "status": "completed", "task_obligation": {"version": 2}}

    @contextmanager
    def selected(*args, **kwargs):
        yield Client()

    monkeypatch.setattr(core, "_client", selected)
    assert core.main(["--local", "claim-status", task_id]) == 1
    result = capsys.readouterr()
    assert not result.out and "invalid task obligation evidence" in result.err
