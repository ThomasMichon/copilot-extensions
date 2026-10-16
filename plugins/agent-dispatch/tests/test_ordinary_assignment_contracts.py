"""Producer compatibility and historical reservation boundaries."""

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
        _start(assigned)
        assert calls.count("resume") == calls.count("send") == 1
        assert not queue.get_reservation(retry.key).conversation_retired
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
