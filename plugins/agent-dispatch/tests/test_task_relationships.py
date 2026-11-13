"""Original-queue and audited-assignment proof through the actual callback boundary."""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as core
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from agent_dispatch.task_obligations import read_snapshot, valid_snapshot
from agent_dispatch.task_relationships import AssignmentSubject


@pytest.fixture
def assigned(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("work", repo="project", require_verification=True)
    queue.claim_one("machine/worktree", task_id=task.id)
    value = read_snapshot(queue, task.id)
    envelope = {
        "version": 2, "token": "t" * 43, "creator": "machine/project/worktree",
        "request_id": "assignment-one", "phase": "bound",
        "recovery": {
            "schema": "dispatch-task-assignment", "version": 1, "authority": "local",
            "queue_identity": value["queue_identity"], "task_id": task.id,
        },
        "identity": {
            "assignment_event_id": value["assignment"]["event_id"], "worker_id": "machine/worktree",
        },
    }
    return queue, task.id, envelope


def encoded(envelope):
    return "l1." + base64.urlsafe_b64encode(json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")).decode("ascii").rstrip("=")


def subject(envelope):
    return AssignmentSubject.decode(encoded(envelope), authority=envelope["recovery"]["authority"])


def verify(queue, task_id, envelope):
    parsed = subject(envelope)
    return parsed.verification({"task_obligation": read_snapshot(
        queue, task_id, assignment_event_id=parsed.assignment_event_id,
    )})


def test_submission_and_lease_expiry_retain_relationship_until_confirmation(assigned):
    queue, task_id, envelope = assigned
    queue.start(task_id, "machine/worktree", owner_session_id="native")
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET lease_expires_at=0 WHERE id=?", (task_id,))
    assert not verify(queue, task_id, envelope)["relationship_verification"]["retired"]
    queue.complete(task_id, "machine/worktree", result={"answer": "proposed"})
    assert verify(queue, task_id, envelope) == {
        "exists": True, "state": "submitted",
        "relationship_verification": {
            "version": 1, "subject": "dispatch-local-task:" + encoded(envelope),
            "retired": False, "quiescent": False,
        },
    }
    queue.confirm(task_id)
    assert verify(queue, task_id, envelope)["relationship_verification"]["retired"]
    assert verify(TaskQueue(queue.db_path), task_id, envelope)["relationship_verification"]["retired"]


def test_only_original_worker_unbound_unassignment_proves_retirement(assigned):
    queue, task_id, envelope = assigned
    queue.yield_task(task_id, "machine/worktree")
    assert verify(queue, task_id, envelope)["relationship_verification"]["retired"]
    queue.claim_one("other/worktree", task_id=task_id)
    assert not verify(queue, task_id, envelope)["relationship_verification"]["retired"]
    queue.yield_task(task_id, "other/worktree")
    assert not verify(queue, task_id, envelope)["relationship_verification"]["retired"]
    queue.claim_one("machine/worktree", task_id=task_id)
    queue.start(task_id, "machine/worktree")
    queue.complete(task_id, "machine/worktree")
    queue.confirm(task_id)
    # A real task completion can discharge its earlier audited assignment too.
    assert verify(queue, task_id, envelope)["relationship_verification"]["retired"]


@pytest.mark.parametrize("machine,worktree", [
    ("machine:slot", "worktree"), ("machine:slot", "nested/worktree"),
])
def test_qualified_owner_aliases_preserve_exact_worktrees_worker_identity(assigned, machine, worktree):
    queue, task_id, envelope = assigned
    queue.yield_task(task_id, "machine/worktree")
    worker = f"{machine}/{worktree}"
    queue.claim_one(worker, task_id=task_id)
    envelope["creator"] = f"{machine}/project/{worktree}"
    assignment = read_snapshot(queue, task_id)["assignment"]
    envelope["identity"] = {
        "assignment_event_id": assignment["event_id"], "worker_id": worker,
    }
    queue.yield_task(task_id, worker)
    assert verify(queue, task_id, envelope)["relationship_verification"]["retired"]


@pytest.mark.parametrize("length", [31, 32, 128, 129])
def test_canonical_token_range_preserves_completed_relationship_settlement(assigned, length):
    queue, task_id, envelope = assigned
    envelope["token"] = "t" * length
    queue.start(task_id, "machine/worktree")
    queue.complete(task_id, "machine/worktree")
    queue.confirm(task_id)
    if length in (32, 128):
        assert verify(queue, task_id, envelope)["relationship_verification"]["retired"]
    else:
        with pytest.raises(DispatchError, match="refused"):
            verify(queue, task_id, envelope)


@pytest.mark.parametrize("state", ["suspended", "abandoned", "dead_letter", "unknown"])
def test_other_states_never_retire_task_responsibility(assigned, state):
    queue, task_id, envelope = assigned
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET status=?, owner=NULL WHERE id=?", (state, task_id))
    assert not verify(queue, task_id, envelope)["relationship_verification"]["retired"]


@pytest.mark.parametrize("change", ["queue", "event", "worker", "missing", "legacy", "bool_version"])
def test_foreign_or_unqualified_provider_cannot_retire(assigned, change):
    queue, task_id, envelope = assigned
    queue.yield_task(task_id, "machine/worktree")
    value = read_snapshot(queue, task_id)
    if change == "queue":
        value["queue_identity"] = "0" * 32
    elif change == "event":
        value["assignment"]["event_id"] += 1
    elif change == "worker":
        value["assignment"]["worker_id"] = "other/worktree"
    elif change == "missing":
        value["assignment"] = None
    elif change == "legacy":
        value.pop("queue_identity")
    else:
        value["version"] = True
    with pytest.raises(DispatchError, match="unconfirmed"):
        subject(envelope).verification({"task_obligation": value})


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 1), ("phase", "pending"), ("creator", "other/project/worktree"),
    ("creator", "machine/project/worktree#session"), ("request_id", "../bad"), ("token", "bad"),
    ("recovery", []), ("identity", []),
])
def test_malformed_subject_never_qualifies(assigned, field, value):
    _, _, envelope = assigned
    envelope[field] = value
    with pytest.raises(DispatchError, match="refused"):
        AssignmentSubject.decode(encoded(envelope), authority="local")


@pytest.mark.parametrize("raw", ["l1.bad", "l1.", "l1." + "a" * 4096])
def test_invalid_encoding_is_refused(raw):
    with pytest.raises(DispatchError):
        AssignmentSubject.decode(raw, authority="local")


def test_duplicate_noncanonical_and_wrong_writer_envelopes_are_refused(assigned):
    _, _, envelope = assigned
    raw = json.dumps(envelope)
    duplicate = '{"version":2,' + raw[1:]
    for data in (raw, duplicate):
        bare = "l1." + base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")
        with pytest.raises(DispatchError):
            AssignmentSubject.decode(bare, authority="local")
    for authority in ("legacy", "central", "pinned_url"):
        with pytest.raises(DispatchError):
            AssignmentSubject.decode(encoded(envelope), authority=authority)


@pytest.mark.parametrize("authority", ["local", "central"])
def test_real_route_client_parser_and_callback_keep_original_writer(assigned, monkeypatch, capsys, authority):
    queue, task_id, envelope = assigned
    envelope["recovery"]["authority"] = authority
    queue.yield_task(task_id, "machine/worktree")
    before = queue.events(task_id)
    requests = []
    with TestClient(create_app(queue)) as api:
        def handle(request):
            requests.append((request.method, request.url.path, dict(request.url.params)))
            response = api.get(request.url.path, params=dict(request.url.params))
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient("http://coordinator.invalid", transport=httpx.MockTransport(handle)) as client:
            @contextmanager
            def selected(args, **options):
                assert options == {"ensure": False, "allow_ssh_failover": False, "read_only": True}
                assert (args.local, args.shared) == (authority == "local", authority == "central")
                yield client

            monkeypatch.setattr(core, "_client", selected)
            assert core.main([
                "--local" if authority == "local" else "--shared", "claim-status", encoded(envelope),
            ]) == 0
    result = json.loads(capsys.readouterr().out)
    proof = result["relationship_verification"]
    assert proof["subject"] == f"dispatch-{authority}-task:" + encoded(envelope)
    assert proof["retired"] and proof["quiescent"]
    assert queue.events(task_id) == before
    assert requests == [("GET", f"/tasks/{task_id}", {
        "obligation_assignment_event_id": str(envelope["identity"]["assignment_event_id"]),
    })]


@pytest.mark.parametrize("status", [404, 401, 503])
def test_missing_or_unavailable_original_task_never_claims_absence(assigned, monkeypatch, capsys, status):
    _, _, envelope = assigned

    class Client:
        def get(self, *args, **kwargs):
            raise DispatchError(status, "unconfirmed task")

    @contextmanager
    def selected(*args, **kwargs):
        yield Client()

    monkeypatch.setattr(core, "_client", selected)
    assert core.main(["--local", "claim-status", encoded(envelope)]) == 1
    result = capsys.readouterr()
    assert not result.out and str(status) in result.err


def test_selected_audit_event_is_exact_scoped_and_still_one_snapshot(assigned):
    queue, task_id, envelope = assigned
    original = deepcopy(read_snapshot(queue, task_id)["assignment"])
    queue.yield_task(task_id, "machine/worktree")
    queue.claim_one("other/worktree", task_id=task_id)
    assert read_snapshot(queue, task_id)["assignment"]["worker_id"] == "other/worktree"
    exact = read_snapshot(queue, task_id, assignment_event_id=original["event_id"])
    assert exact["assignment"] == original and valid_snapshot(exact, task_id=task_id)
    other = queue.create("other", repo="project")
    assert read_snapshot(queue, other.id, assignment_event_id=original["event_id"])["assignment"] is None
    with TestClient(create_app(queue)) as api:
        for invalid in ("0", "-1", str(2**63), "true"):
            assert api.get(f"/tasks/{task_id}", params={
                "obligation_assignment_event_id": invalid,
            }).status_code == 422
        assert api.get(f"/tasks/{task_id}", params={
            "obligation_assignment_event_id": original["event_id"], "native_restart_policy": "true",
        }).status_code == 422


def test_assignment_lookup_is_indexed_and_old_snapshot_shape_remains_valid(assigned):
    queue, task_id, _ = assigned
    with queue._connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id, worker FROM task_events"
            " WHERE task_id=? AND to_status='claimed' AND note='claim' AND from_status IS NOT to_status"
            " ORDER BY id DESC LIMIT 1", (task_id,),
        ).fetchall()
    assert any("SEARCH task_events USING INDEX idx_task_events_" in row[3] for row in plan)
    legacy = read_snapshot(queue, task_id)
    legacy.pop("queue_identity")
    legacy.pop("assignment")
    assert valid_snapshot(legacy, task_id=task_id)


@pytest.mark.parametrize("unsupported", ["old_coordinator", "ignored_selection", "transport"])
def test_rollback_or_unreachable_provider_retains_relationship(assigned, monkeypatch, capsys, unsupported):
    queue, task_id, envelope = assigned
    queue.yield_task(task_id, "machine/worktree")
    value = read_snapshot(queue, task_id)
    if unsupported == "old_coordinator":
        value.pop("assignment")
        value.pop("queue_identity")
    else:
        value["assignment"]["event_id"] += 1

    class Client:
        def get(self, *args, **kwargs):
            if unsupported == "transport":
                raise httpx.ConnectError("original writer unavailable")
            return {"task_obligation": value}

    @contextmanager
    def selected(*args, **kwargs):
        yield Client()

    monkeypatch.setattr(core, "_client", selected)
    assert core.main(["--local", "claim-status", encoded(envelope)]) == 1
    result = capsys.readouterr()
    assert not result.out and ("unconfirmed" in result.err or "unavailable" in result.err)


@pytest.mark.parametrize("flags", [[], ["--url", "http://other.invalid"], ["--local"]])
def test_invalid_authority_or_subject_never_constructs_client(assigned, monkeypatch, capsys, flags):
    _, _, envelope = assigned
    monkeypatch.delenv("AGENT_DISPATCH_TASK_AUTHORITY", raising=False)
    if flags == ["--local"]:
        envelope["identity"]["assignment_event_id"] = True
    monkeypatch.setattr(core, "_client", lambda *a, **k: pytest.fail("invalid subject reached client"))
    assert core.main([*flags, "claim-status", encoded(envelope)]) == 1
    result = capsys.readouterr()
    assert not result.out and ("refused" in result.err or "explicit" in result.err)


def test_allocator_placement_actor_is_not_a_confirmed_worktree_assignment(assigned):
    queue, _, envelope = assigned
    from tests.test_pool_worker_decline import prepared_worker

    task_id, binding = prepared_worker(queue)
    with queue._connect() as conn:
        event = conn.execute(
            "SELECT id FROM task_events WHERE task_id=? AND to_status='claimed' ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    value = read_snapshot(queue, task_id, assignment_event_id=event["id"])
    assert value["assignment"] is None
    envelope["recovery"]["task_id"] = task_id
    envelope["identity"] = {
        "assignment_event_id": event["id"], "worker_id": binding["worker_id"],
    }
    host, worktree = binding["worker_id"].split("/")
    envelope["creator"] = f"{host}/project/{worktree}"
    with pytest.raises(DispatchError, match="unconfirmed"):
        subject(envelope).verification({"task_obligation": value})
