"""Producer compatibility and historical reservation boundaries."""

from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import bridge
from agent_dispatch.ordinary_agent_storage import reservation_native_facts
from agent_dispatch.supervisor import Supervisor
from tests.test_ordinary_assignment import _legacy, _start, assigned as assigned, create_app_for
from tests.test_supervisor import QueueBackedClient

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


@pytest.mark.parametrize("change", [
    "missing-attribution", "missing-opacity", "opaque", "invalid-task", "invalid-opacity",
])
def test_ordinary_assignment_attribution_capability_is_required(assigned, change):
    queue, task, _, record, _, calls = assigned
    if change == "missing-attribution":
        record.pop("dispatch_attempt")
    elif change == "missing-opacity":
        record.pop("dispatch_attempt_opaque")
    elif change == "opaque":
        record["dispatch_attempt_opaque"] = True
    elif change == "invalid-task":
        record["dispatch_attempt"]["task_id"] = None
    else:
        record["dispatch_attempt_opaque"] = 0
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        _start(assigned)
    assert calls == ["record"]
    assert queue.get_reservation(task["spawn_reservation_key"]).agent_info is None


@pytest.mark.parametrize("driver", ["cli", "fleet"])
@pytest.mark.parametrize("head", ["native-a", "native-b", None])
def test_ordinary_assignment_retry_keeps_unreceipted_historical_handle(assigned, driver, head):
    queue, task, _, record, _, calls = assigned
    old_key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET driver=?,session_handle='native-a' WHERE key=?",
            (driver, old_key),
        )
    queue.defer_spawn(old_key, detail="interrupted legacy launch")
    retry, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and retry.session_handle == "native-a" and retry.agent_info is None
    assert retry.driver == "ordinary-bridge"
    task["spawn_reservation_key"] = retry.key
    record.update(last_session_id=head, session_count=int(head is not None))
    if driver == "cli" and head == "native-a":
        _start(assigned)
        assert calls.count("resume") == calls.count("send") == 1
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy):
            _start(assigned)
        assert "resume" not in calls and "send" not in calls
    assert "create" not in calls
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM acquisition_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("resumed", [False, True])
def test_ordinary_assignment_null_stored_path_still_fences_actual_workspace(assigned, resumed):
    queue, task, _, record, response, _ = assigned
    key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET worktree_path=NULL WHERE key=?", (key,))
    before = queue.get_reservation(key)
    if resumed:
        facts = {"session_info": {**response, "target_dir": record["path"] + "-foreign"}}
    else:
        facts = {"agent_info": {
            "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
            "connection": {
                "worktree_id": record["id"], "repo": record["repo"],
                "native_session_id": record["last_session_id"],
                "target_dir": record["path"] + "-foreign",
            },
        }}
    with TestClient(create_app_for(queue)) as api:
        result = api.post(f"/spawn-reservations/{key}/{'resumed' if resumed else 'created'}-agent",
                          json={"task_generation": task["generation"],
                                "worktree_record": record, **facts})
        assert result.status_code == 409 and "exact ground workspace" in result.json()["detail"]
    assert queue.get_reservation(key) == before


@pytest.mark.parametrize("missing", ["project", "path", "both"])
@pytest.mark.parametrize("retired", [False, True])
def test_ordinary_assignment_supervisor_defers_missing_metadata_without_native_effects(
    assigned, missing, retired,
):
    queue, task, _, record, _, calls = assigned
    key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET allocation_project=?,worktree_path=?,"
            "conversation_retired=? WHERE key=?",
            (None if missing in {"project", "both"} else record["repo"],
             None if missing in {"path", "both"} else record["path"], int(retired), key),
        )
    before = queue.get_reservation(key)
    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("recovery must not spawn"),
        repo="queue-lane", supervisor_id="supervisor", reserving_timeout=0,
        local_body_verdict_fn=lambda _: pytest.fail("no native probe authority"),
    )
    assert supervisor.reconcile_reserving() == 1
    deferred = queue.get_reservation(key)
    assert deferred.state == "deferred" and not deferred.release_requested
    assert not supervisor._pool_reservations(state="reserving,spawned,cold,releasing")
    retry, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and retry.worktree == before.worktree
    assert retry.creation_key == before.creation_key
    assert retry.conversation_retired is retired
    assert not calls
    if retired:
        facts = reservation_native_facts(queue, retry.key)
        assert facts["ordinary_assignment_error"]
        assert facts["inherited_retirement_reconcilable"] is False


@pytest.mark.parametrize("head", ["native-a", "native-b", "native-c", None])
def test_ordinary_assignment_inherited_retirement_checks_actual_current_head(assigned, head):
    queue, task, _, record, response, calls = assigned
    _legacy(queue, task)
    old_key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state='failed',release_requested=1,"
            "exclusive_released=1 WHERE key=?", (old_key,),
        )
        conn.execute("UPDATE tasks SET owner_session_id='native-b' WHERE id=?", (task["id"],))
    retry, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and retry.conversation_retired and retry.session_handle is None
    task["spawn_reservation_key"] = retry.key
    record.update(last_session_id=head, session_count=2 if head else 0)
    if head == "native-b":
        response.update(acp_session_id=head, durable_session_id=head)
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no immutable ordinary invocation"):
            _start(assigned)
        assert calls == ["record"]
        assert queue.get_reservation(retry.key).conversation_retired
        facts = reservation_native_facts(queue, retry.key)
        assert facts["retired_native_session_ids"] == ["native-a"]
        assert facts["known_native_session_ids"] == ["native-a", "native-b"]
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy):
            _start(assigned)
        assert calls == ["record"]
    assert "create" not in calls


def test_ordinary_assignment_unknown_retired_bridge_native_stays_fenced(assigned):
    queue, task, _, record, _, calls = assigned
    old_key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state='failed',release_requested=1,"
            "exclusive_released=1,session_handle='local-body:unknown-old' WHERE key=?", (old_key,),
        )
    retry, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and retry.conversation_retired
    task["spawn_reservation_key"] = retry.key
    record.update(last_session_id="native-b", session_count=2)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no proven native"):
        _start(assigned)
    assert not calls


def test_ordinary_assignment_created_route_requires_record_for_unrecorded_target(assigned):
    queue, task, _, record, _, _ = assigned
    _legacy(queue, task)
    key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET worktree_path=NULL WHERE key=?", (key,))
    before = queue.get_reservation(key)
    with TestClient(create_app_for(queue)) as api:
        response = api.post(f"/spawn-reservations/{key}/created-agent", json={
            "task_generation": task["generation"],
            "agent_info": {
                "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
                "connection": {
                    "worktree_id": record["id"], "repo": record["repo"],
                    "native_session_id": record["last_session_id"],
                    "target_dir": record["path"] + "-foreign",
                },
            },
        })
    assert response.status_code == 409
    assert "requires the exact ground owner record" in response.json()["detail"]
    assert queue.get_reservation(key) == before


@pytest.mark.parametrize("head", ["native-a", "native-b"])
def test_ordinary_assignment_retirement_resolves_carried_cli_producer_first(assigned, head):
    queue, task, _, record, response, calls = assigned
    old_key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET driver='cli',session_handle='native-a' WHERE key=?",
            (old_key,),
        )
    queue.defer_spawn(old_key, detail="legacy CLI transport interrupted")
    carried, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and carried.session_handle == "native-a" and carried.agent_info is None
    queue.fail_spawn(carried.key, detail="carried conversation retired", release_requested=True)
    current, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and current.conversation_retired and current.session_handle is None
    task["spawn_reservation_key"] = current.key
    record.update(last_session_id=head, session_count=2)
    response.update(acp_session_id=head, durable_session_id=head)
    facts = reservation_native_facts(queue, current.key)
    assert facts["retired_native_session_ids"] == ["native-a"]
    assert facts["ordinary_assignment_error"] is None
    if head == "native-b":
        _start(assigned)
        assert calls.count("resume") == calls.count("send") == 1
        assert queue.get_reservation(current.key).conversation_retired is False
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="known or retired"):
            _start(assigned)
        assert calls == ["record"]
    assert "create" not in calls


@pytest.mark.parametrize("head", ["native-a", "native-b", None])
def test_ordinary_assignment_receipt_before_handle_keeps_terminal_retirement(assigned, head):
    queue, task, _, record, response, calls = assigned
    _legacy(queue, task)
    old_key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET session_handle=NULL WHERE key=?", (old_key,))
    queue.fail_spawn(old_key, detail="interrupted before recording handle", release_requested=True)
    current, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and current.session_handle is None
    task["spawn_reservation_key"] = current.key
    record.update(last_session_id=head, session_count=2 if head else 0)
    response.update(acp_session_id=head, durable_session_id=head)
    facts = reservation_native_facts(queue, current.key)
    assert facts["retired_native_session_ids"] == ["native-a"]
    if head == "native-b":
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no immutable ordinary invocation"):
            _start(assigned)
        assert calls == ["record"]
        assert queue.get_reservation(current.key).agent_info is None
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy):
            _start(assigned)
        assert "resume" not in calls and "send" not in calls
    assert "create" not in calls


def test_ordinary_assignment_handleless_release_without_native_stays_unknown(assigned):
    queue, task, _, record, _, calls = assigned
    queue.fail_spawn(task["spawn_reservation_key"], detail="unproven release", release_requested=True)
    current, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and current.session_handle is None
    task["spawn_reservation_key"] = current.key
    record.update(last_session_id="native-b", session_count=2)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no proven native"):
        _start(assigned)
    assert not calls


@pytest.mark.parametrize("head", ["native-a", "native-b"])
@pytest.mark.parametrize("tagged_owner", [False, True])
@pytest.mark.parametrize("transfers", [1, 2])
def test_ordinary_assignment_cross_task_handleless_retirement_keeps_exact_origin(
    assigned, head, tagged_owner, transfers,
):
    queue, task, _, record, response, calls = assigned
    _legacy(queue, task)
    original = queue.get_reservation(task["spawn_reservation_key"])
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET session_handle=NULL WHERE key=?", (original.key,))
    queue.fail_spawn(original.key, detail="receipt before handle write", release_requested=True)
    for transfer in range(transfers):
        next_task = queue.create("different logical task", repo="queue-lane", exclusive_key="review-slot")
        current, admitted = queue.reserve_spawn(next_task.id, driver="ordinary-bridge")
        assert admitted and current.allocation_reservation_key == original.key
        if transfer + 1 < transfers:
            queue.defer_spawn(current.key, detail="next exclusive-lane task")
    assert admitted and current.worktree == original.worktree and current.session_handle is None
    assert current.creation_key != original.creation_key
    assert current.allocation_reservation_key == original.key
    task.clear()
    task.update(asdict(next_task), spawn_reservation_key=current.key)
    record.update(last_session_id=head, session_count=2)
    if not tagged_owner:
        record["dispatch_attempt"] = None
    response.update(acp_session_id=head, durable_session_id=head)
    unrelated = queue.create("unrelated workspace history", repo="queue-lane", exclusive_key="other-slot")
    unrelated_reservation, _ = queue.reserve_spawn(unrelated.id, driver="ordinary-bridge")
    queue.record_spawn_worktree(unrelated_reservation.key, "worktree-a", ownership="targeted")
    _legacy(queue, {**asdict(unrelated), "spawn_reservation_key": unrelated_reservation.key},
            native="unrelated-native", handle="unrelated-bridge")
    queue.fail_spawn(unrelated_reservation.key, detail="unrelated history", release_requested=True)
    facts = reservation_native_facts(queue, current.key)
    assert facts["known_native_session_ids"] == facts["retired_native_session_ids"] == ["native-a"]
    if head == "native-b":
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no immutable ordinary invocation"):
            _start(assigned)
        assert calls == ["record"]
        assert queue.get_reservation(current.key).agent_info is None
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="known or retired"):
            _start(assigned)
        assert "resume" not in calls and "send" not in calls
    assert "create" not in calls


@pytest.mark.parametrize("origin", [None, "self", "missing-predecessor"])
def test_ordinary_assignment_legacy_handleless_inheritance_without_origin_refuses(assigned, origin):
    queue, task, _, record, _, calls = assigned
    original = queue.get_reservation(task["spawn_reservation_key"])
    queue.defer_spawn(original.key, detail="handleless inherited workspace")
    next_task = queue.create("different work", repo="queue-lane", exclusive_key="review-slot")
    current, admitted = queue.reserve_spawn(next_task.id, driver="ordinary-bridge")
    assert admitted and current.session_handle is None
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET allocation_reservation_key=? WHERE key=?",
                     (current.key if origin == "self" else origin, current.key))
    task.clear()
    task.update(asdict(next_task), spawn_reservation_key=current.key)
    record.update(last_session_id="native-a", dispatch_attempt=None)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="native origin is unproven"):
        _start(assigned)
    assert not calls


def test_ordinary_assignment_unproven_intermediate_legacy_origin_refuses(assigned):
    queue, task, _, record, _, calls = assigned
    _legacy(queue, task)
    original = queue.get_reservation(task["spawn_reservation_key"])
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET session_handle=NULL WHERE key=?", (original.key,))
    queue.fail_spawn(original.key, detail="receipt before handle", release_requested=True)
    intermediate = queue.create("legacy task", repo="queue-lane", exclusive_key="review-slot")
    legacy, _ = queue.reserve_spawn(intermediate.id, driver="cli")
    assert legacy.worktree == original.worktree and legacy.allocation_reservation_key == legacy.key
    queue.defer_spawn(legacy.key, detail="legacy carry has no root provenance")
    next_task = queue.create("ordinary task", repo="queue-lane", exclusive_key="review-slot")
    current, admitted = queue.reserve_spawn(next_task.id, driver="ordinary-bridge")
    assert admitted and current.allocation_reservation_key == legacy.key
    task.clear()
    task.update(asdict(next_task), spawn_reservation_key=current.key)
    record.update(last_session_id="native-a", dispatch_attempt=None)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="native origin is unproven"):
        _start(assigned)
    assert not calls


@pytest.mark.parametrize("head", ["native-a", "native-b"])
def test_ordinary_assignment_exclusive_carry_keeps_original_handle_native(assigned, head):
    queue, task, _, record, _, calls = assigned
    _legacy(queue, task)
    first = queue.get_reservation(task["spawn_reservation_key"])
    queue.defer_spawn(first.key, detail="retained exclusive lane")
    next_task = queue.create("different logical task", repo="queue-lane", exclusive_key="review-slot")
    current, admitted = queue.reserve_spawn(next_task.id, driver="ordinary-bridge")
    assert admitted and current.creation_key != first.creation_key
    assert current.worktree == first.worktree and current.session_handle == first.session_handle
    task.clear()
    task.update(asdict(next_task), spawn_reservation_key=current.key)
    record.update(last_session_id=head, dispatch_attempt=None)
    facts = reservation_native_facts(queue, current.key)
    assert facts["known_native_session_ids"] == ["native-a"]
    if head == "native-a":
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no immutable ordinary invocation"):
            _start(assigned)
        assert calls == ["record"]
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="known or retired"):
            _start(assigned)
        assert calls == ["record"]
    assert "create" not in calls


@pytest.mark.parametrize("assigned", [{"dedup_key": "logical-work"}], indirect=True)
@pytest.mark.parametrize("foreign", [False, True])
def test_ordinary_assignment_dedup_reemission_preserves_original_owner(assigned, foreign):
    queue, task, _, record, _, calls = assigned
    first = queue.get_reservation(task["spawn_reservation_key"])
    queue.defer_spawn(first.key, detail="retained ground allocation")
    queue.claim_one("first-worker", task_id=task["id"])
    queue.start(task["id"], "first-worker", owner_session_id="native-a")
    queue.complete(task["id"], "first-worker")
    _legacy(queue, task)
    next_task = queue.create("re-emitted work", repo="queue-lane",
                             exclusive_key="review-slot", dedup_key="logical-work")
    current, admitted = queue.reserve_spawn(next_task.id, driver="ordinary-bridge")
    assert admitted and next_task.id != first.task_id
    assert current.creation_key == first.creation_key
    assert current.allocation_reservation_key == first.key
    task.clear()
    task.update(asdict(next_task), spawn_reservation_key=current.key)
    if foreign:
        record["dispatch_attempt"]["task_id"] = "unrelated-allocation"
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="identity, holder"):
            _start(assigned)
        assert calls == ["record"]
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no immutable ordinary invocation"):
            _start(assigned)
        assert calls == ["record"]
        assert queue.get(next_task.id).status == "queued"
    assert "create" not in calls
