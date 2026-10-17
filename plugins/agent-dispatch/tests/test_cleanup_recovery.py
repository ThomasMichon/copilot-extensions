"""Held housekeeping recovery never reopens the business task."""
from dataclasses import asdict
import json

import pytest

from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


def _held_cleanup(tmp_path, *, labels=None):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("finished work", exclusive_key="logical-work", labels=labels)
    reservation, _ = queue.reserve_spawn(task.id, now=10)
    queue.record_spawn_worktree(
        reservation.key, "worker-exact", ownership="created",
        creating_host="host", driver="supervisor",
        allocation_project="fixture-project", worktree_path="fixture-checkout",
    )
    queue.record_spawn(reservation.key, session_handle="session-exact")
    queue.claim_one("host/worker-exact", task_id=task.id)
    queue.start(task.id, "host/worker-exact", owner_session_id="native-session")
    queue.complete(task.id, "host/worker-exact", result_ref="artifact:kept")
    queue.confirm(task.id, actor="operator")
    payload = {
        "action": "preserved", "reason": "cleanup-retry-exhausted",
        "cleanup_kind": "attempt", "attempts": 12, "next_attempt_at": 0,
        "acp_session_id": "session-exact",
        "session_end": {"state": "complete", "session_id": "native-session"},
        "worktree_cleanup": {"state": "held", "attempts": 12, "outcome": {"action": "failed"}},
    }
    queue.settle_spawn(
        reservation.key, conclusion_state="held",
        conclusion_detail=json.dumps(payload),
        release_requested=True,
    )
    return queue, task.id, reservation.key


@pytest.fixture
def held(tmp_path):
    return _held_cleanup(tmp_path)


def preview(held, **over):
    queue, task_id, key = held
    return queue.recover_held_cleanup(
        key, expected_generation=queue.get(task_id).generation,
        current_host="host", **over,
    )


def apply(held, plan, **over):
    return preview(
        held, apply=True, reason="underlying fault corrected", request_id="recovery-1",
        expected_preview=plan["preview_token"], **over,
    )


def test_held_cleanup_preview_and_idempotent_apply_preserve_task(held):
    queue, task_id, key = held
    before = asdict(queue.get(task_id))
    attachments = queue.attachment_history(task_id)
    progress = queue.progress_log(task_id)
    events = queue.events(task_id)
    prior = asdict(queue.get_reservation(key))
    plan = preview(held)
    assert plan["eligible"] is True
    assert asdict(queue.get_reservation(key)) == prior
    first = apply(held, plan)
    assert first["scheduled"] is True
    assert apply(held, plan) == first
    assert asdict(queue.get(task_id)) == before
    assert queue.attachment_history(task_id) == attachments
    assert queue.progress_log(task_id) == progress
    assert queue.events(task_id)[:len(events)] == events
    retried = queue.get_reservation(key)
    detail = json.loads(retried.conclusion_detail)
    assert detail["worktree_cleanup"]["state"] == "pending"
    assert detail["worktree_cleanup"]["attempts"] == 0
    assert detail["session_end"]["state"] == "complete"
    _, claimed, token = queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
    assert claimed and token
    queue.validate_spawn_conclusion_claim(key, token, claimant_machine="host")
    with queue._connect() as conn:
        history = conn.execute("SELECT prior_detail FROM cleanup_recoveries").fetchone()
    assert history["prior_detail"] == prior["conclusion_detail"]


@pytest.mark.parametrize("column,value", [
    ("status", "submitted"), ("status", "started"), ("status", "queued"),
    ("generation", 99),
])
def test_recovery_rejects_changed_task_fence(held, column, value):
    queue, task_id, _ = held
    plan = preview(held)
    with queue._connect() as conn:
        conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))
    with pytest.raises(TaskError):
        apply(held, plan)


@pytest.mark.parametrize("column,value", [
    ("worktree_ownership", "unknown"), ("worktree_ownership", "reused"),
    ("creating_host", "another-host"), ("driver", None), ("conclusion_detail", "{}"),
    ("state", "spawned"),
])
def test_recovery_preserves_unknown_or_active_ownership(held, column, value):
    queue, _, key = held
    with queue._connect() as conn:
        conn.execute(f"UPDATE spawn_reservations SET {column}=? WHERE key=?", (value, key))
    with pytest.raises(TaskError):
        preview(held)


def test_recovery_rejects_newer_allocation_even_when_settled(held):
    queue, _, key = held
    plan = preview(held)
    newer = queue.create("newer")
    reservation, _ = queue.reserve_spawn(newer.id, now=20)
    queue.record_spawn_worktree(
        reservation.key, "worker-exact", ownership="created", creating_host="host",
    )
    queue.settle_spawn(reservation.key)
    with pytest.raises(TaskError, match="allocation"):
        apply(held, plan)
    assert queue.get_reservation(key).conclusion_state == "held"


@pytest.mark.parametrize("obligation", [0, "unknown", -1])
def test_recovery_rejects_missing_existing_cleanup_obligation(held, obligation):
    queue, task_id, key = held
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET release_requested=? WHERE key=?", (obligation, key))
    before = asdict(queue.get_reservation(key))
    events = queue.events(task_id)
    with pytest.raises(TaskError, match="existing.*obligation"):
        preview(held)
    with pytest.raises(TaskError, match="existing.*obligation"):
        queue.recover_held_cleanup(
            key, expected_generation=queue.get(task_id).generation, current_host="host",
            apply=True, expected_preview="unauthorized-preview", request_id="unauthorized-request",
            reason="must not create cleanup authority",
        )
    assert asdict(queue.get_reservation(key)) == before
    assert queue.cleanup_recovery_history(key) == []
    assert queue.events(task_id) == events


@pytest.mark.parametrize("settled", [False, True])
@pytest.mark.parametrize("reserved_at", [10, 20])
def test_recovery_newer_allocation_is_held_once_by_real_supervisor(held, monkeypatch, settled, reserved_at):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, task_id, key = held
    queue.settle_spawn(key, release_requested=True)
    before = asdict(queue.get(task_id))
    apply(held, preview(held))
    newer = queue.create("newer allocation")
    reservation, _ = queue.reserve_spawn(newer.id, now=reserved_at)
    # Fault-inject delayed allocation publication after recovery acceptance.
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree='worker-exact' WHERE key=?", (reservation.key,),
        )
    if settled:
        queue.settle_spawn(reservation.key)
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(app)
        claims = []
        original = client.claim_spawn_conclusion_retry

        def claim(exact_key, **kwargs):
            claims.append(exact_key)
            return original(exact_key, **kwargs)

        monkeypatch.setattr(client, "claim_spawn_conclusion_retry", claim)

        def forbidden(*args, **kwargs):
            pytest.fail("newer allocation must not permit cleanup or task spawn")

        supervisor = Supervisor(
            client, spawn_fn=forbidden, attempt_conclusion_fn=forbidden,
            conclusion_fn=forbidden, repo=TEST_REPO, machine="host", nudge=False,
        )
        assert supervisor.release_requested_bodies() == 0
        preserved = queue.get_reservation(key)
        assert preserved.conclusion_state == "held"
        detail = json.loads(preserved.conclusion_detail)
        assert detail["reason"] == "worktree-carried-by-newer-reservation"
        assert detail["blocking_reservation_key"] == reservation.key
        assert supervisor.release_requested_bodies() == 0
        assert claims == [key]
        assert asdict(queue.get(task_id)) == before
        assert len(queue.cleanup_recovery_history(key)) == 1


def test_recovery_revalidates_generation_after_cleanup_lease(held):
    queue, task_id, key = held
    apply(held, preview(held))
    _, claimed, token = queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
    assert claimed
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (task_id,))
    with pytest.raises(TaskError, match="generation"):
        queue.validate_spawn_conclusion_claim(key, token, claimant_machine="host")


@pytest.mark.parametrize("column,value", [
    ("allocation_project", "other-project"), ("worktree_path", "other-checkout"),
    ("allocation_reservation_key", "other-reservation"), ("exclusive_key", "other-exclusive"),
    ("allocation_revision", 99), ("creation_key", "other-creation"),
    ("inherited_worktree", "other-inherited"),
    ("release_requested", 0),
])
@pytest.mark.parametrize("boundary", ["claim", "validate", "outcome"])
def test_recovery_fences_complete_allocation_identity(held, column, value, boundary):
    queue, task_id, key = held
    before = asdict(queue.get(task_id))
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET allocation_project='fixture-project', "
            "worktree_path='fixture-checkout', allocation_reservation_key=?, "
            "creation_key='fixture-creation', inherited_worktree='fixture-prior' WHERE key=?",
            (key, key),
        )
    apply(held, preview(held))
    token = None
    if boundary != "claim":
        _, claimed, token = queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
        assert claimed
    with queue._connect() as conn:
        conn.execute(f"UPDATE spawn_reservations SET {column}=? WHERE key=?", (value, key))
    detail = queue.get_reservation(key).conclusion_detail
    with pytest.raises(TaskError, match="provenance changed"):
        if boundary == "claim":
            queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
        elif boundary == "validate":
            queue.validate_spawn_conclusion_claim(key, token, claimant_machine="host")
        else:
            queue.record_spawn_conclusion(
                key, conclusion_state="complete", conclusion_detail='{"action":"primed"}',
                claim_token=token, claimant_machine="host",
            )
    assert queue.get_reservation(key).conclusion_detail == detail
    assert asdict(queue.get(task_id)) == before


@pytest.mark.parametrize("payload", [
    {"cleanup_kind": "terminal"},
    {"cleanup_kind": "attempt"},
    {"cleanup_kind": "terminal", "session_end": None},
    {"cleanup_kind": "terminal", "unknown_component": {"state": "held"}},
    {"cleanup_kind": "terminal", "worktree_cleanup": {"state": "complete"}},
])
def test_recovery_requires_incomplete_structured_component(held, payload):
    queue, _, key = held
    raw = json.dumps(payload)
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET conclusion_detail=? WHERE key=?", (raw, key))
    with pytest.raises(TaskError, match="component"):
        preview(held)
    assert queue.get_reservation(key).conclusion_state == "held"
    assert queue.get_reservation(key).conclusion_detail == raw


@pytest.mark.parametrize("field", ["cleanup_kind", "action", "structured-action"])
@pytest.mark.parametrize("value", [[], {}])
def test_recovery_rpc_refuses_non_string_envelope_values(held, field, value):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient, DispatchError
    from agent_dispatch.coordinator import create_app

    queue, task_id, key = held
    payload = json.loads(queue.get_reservation(key).conclusion_detail)
    if field == "action":
        payload = {"action": value, "reason": "recorded failure", "attempts": 12}
    else:
        payload["action" if field == "structured-action" else field] = value
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET conclusion_detail=? WHERE key=?",
                     (json.dumps(payload), key))
    before = asdict(queue.get_reservation(key))
    app = create_app(
        queue, control_token="fixture-control", enable_mcp=False, verification_interval=0,
    )
    with DispatchClient("http://testserver", control_token="fixture-control") as client:
        client._http.close()
        client._http = TestClient(app, raise_server_exceptions=False)
        with pytest.raises(DispatchError) as error:
            client.recover_held_cleanup(
                key, expected_generation=queue.get(task_id).generation, current_host="host",
            )
    assert error.value.status_code == 409
    assert asdict(queue.get_reservation(key)) == before
    assert queue.cleanup_recovery_history(key) == []


@pytest.mark.parametrize("kind", ["worktree", "session", "legacy"])
def test_supervisor_uses_recovery_reset_after_list_claim_race(held, monkeypatch, kind):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, task_id, key = held
    task_before = asdict(queue.get(task_id))
    payload = json.loads(queue.get_reservation(key).conclusion_detail)
    if kind == "legacy":
        payload = {"action": "failed", "reason": "retry failure", "attempts": 11}
    elif kind == "session":
        payload["session_end"] = {"state": "pending", "session_id": "native-session", "attempts": 11}
        payload["worktree_cleanup"] = {"state": "complete", "outcome": {"action": "primed"}}
    else:
        payload["worktree_cleanup"]["state"] = "pending"
        payload["worktree_cleanup"]["attempts"] = 11
    queue.record_spawn_conclusion(key, conclusion_state="pending", conclusion_detail=json.dumps(payload))
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(create_app(queue, enable_mcp=False, verification_interval=0))
        original_claim = client.claim_spawn_conclusion_retry
        claims = []

        def recover_between_list_and_claim(exact_key, **kwargs):
            claims.append(exact_key)
            exhausted = json.loads(queue.get_reservation(key).conclusion_detail)
            exhausted["attempts"] = 12
            for name in ("session_end", "worktree_cleanup"):
                if isinstance(exhausted.get(name), dict) and exhausted[name].get("state") == "pending":
                    exhausted[name].update(state="held", attempts=12)
            _, other_claimed, other_token = queue.claim_spawn_conclusion_retry(
                key, claimant_machine="host",
            )
            assert other_claimed
            queue.record_spawn_conclusion(
                key, conclusion_state="held", conclusion_detail=json.dumps(exhausted),
                claim_token=other_token, claimant_machine="host",
            )
            apply(held, preview(held))
            return original_claim(exact_key, **kwargs)

        monkeypatch.setattr(client, "claim_spawn_conclusion_retry", recover_between_list_and_claim)
        supervisor = Supervisor(
            client, spawn_fn=lambda *a: pytest.fail("business work must not replay"),
            local_end_fn=lambda *a: False,
            attempt_conclusion_fn=lambda *a: {"action": "failed", "reason": "fixture failure"},
            repo=TEST_REPO, machine="host", nudge=False,
        )
        supervisor.release_requested_bodies()
    row = queue.get_reservation(key)
    updated = json.loads(row.conclusion_detail)
    component = updated if kind == "legacy" else updated["session_end" if kind == "session" else "worktree_cleanup"]
    assert row.conclusion_state == "pending"
    assert component["attempts"] == 1
    assert claims == [key]
    assert asdict(queue.get(task_id)) == task_before


@pytest.mark.parametrize("listed_attempts", [11, 12])
def test_full_poll_legacy_cleanup_uses_claimed_recovery_snapshot(tmp_path, monkeypatch, listed_attempts):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    fixture = _held_cleanup(tmp_path, labels=["disposable"])
    queue, task_id, key = fixture
    task_before = asdict(queue.get(task_id))
    queue.record_spawn_conclusion(key, conclusion_state="pending", conclusion_detail=json.dumps({
        "action": "failed", "reason": "legacy failure", "attempts": listed_attempts,
    }))
    claims, conclusions = [], []
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(create_app(queue, enable_mcp=False, verification_interval=0))
        original_claim = client.claim_spawn_conclusion_retry

        def reset_between_list_and_claim(exact_key, **kwargs):
            claims.append(exact_key)
            _, claimed, token = queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
            assert claimed
            queue.record_spawn_conclusion(
                key, conclusion_state="held",
                conclusion_detail=json.dumps({"action": "failed", "reason": "legacy failure", "attempts": 12}),
                claim_token=token, claimant_machine="host",
            )
            apply(fixture, preview(fixture))
            return original_claim(exact_key, **kwargs)

        monkeypatch.setattr(client, "claim_spawn_conclusion_retry", reset_between_list_and_claim)
        supervisor = Supervisor(
            client, spawn_fn=lambda *a: pytest.fail("business work must not replay"),
            conclusion_fn=lambda *a: conclusions.append(a) or {"action": "failed", "reason": "retry"},
            attempt_conclusion_fn=lambda *a: pytest.fail("legacy terminal policy must not change"),
            repo=TEST_REPO, machine="host", disposable_cli_labels=["disposable"],
            heartbeat=False, nudge=False, recover=False, consistency_sweep=False,
        )
        assert supervisor.poll_once() == []
    row = queue.get_reservation(key)
    assert row.conclusion_state == "pending"
    assert json.loads(row.conclusion_detail)["attempts"] == 1
    assert claims == [key]
    assert len(conclusions) == 1
    assert asdict(queue.get(task_id)) == task_before
    assert len(queue.cleanup_recovery_history(key)) == 1


@pytest.mark.parametrize("kind", ["attempt", "terminal", None])
def test_session_only_recovery_never_ends_session_without_lease(held, kind):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient, DispatchError
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, task_id, key = held
    payload = {
        "action": "preserved", "reason": "cleanup-retry-exhausted", "attempts": 12,
        "session_end": {"state": "held", "session_id": "native-session"},
    }
    if kind is not None:
        payload["cleanup_kind"] = kind
    queue.record_spawn_conclusion(key, conclusion_state="held", conclusion_detail=json.dumps(payload))
    before = asdict(queue.get_reservation(key))
    app = create_app(queue, control_token="fixture-control", enable_mcp=False, verification_interval=0)
    with DispatchClient("http://testserver", control_token="fixture-control") as client:
        client._http.close()
        client._http = TestClient(app)
        rejected = None
        try:
            plan = client.recover_held_cleanup(
                key, expected_generation=queue.get(task_id).generation, current_host="host",
            )
            client.recover_held_cleanup(
                key, expected_generation=queue.get(task_id).generation, current_host="host",
                apply=True, expected_preview=plan["preview_token"], request_id="session-only",
                reason="must not schedule an unleased session side effect",
            )
        except DispatchError as exc:
            rejected = exc
        ended = []

        def end_session(session):
            ended.append((session, queue.get_reservation(key).cleanup_claim_token))
            return True

        def no_work(*args, **kwargs):
            return {"action": "skipped", "reason": "fixture-preservation"}

        supervisor = Supervisor(
            client, spawn_fn=no_work, local_end_fn=end_session,
            attempt_conclusion_fn=no_work, conclusion_fn=no_work,
            repo=TEST_REPO, machine="host", nudge=False,
        )
        supervisor.release_requested_bodies()
        assert ended == []
        assert rejected is not None and rejected.status_code == 409
        assert asdict(queue.get_reservation(key)) == before
        assert queue.cleanup_recovery_history(key) == []


@pytest.mark.parametrize("state", ["held", "pending", "complete", False])
@pytest.mark.parametrize("boundary", ["preview", "apply"])
def test_unknown_state_bearing_component_preserves_cleanup_obligation(held, state, boundary):
    queue, task_id, key = held
    plan = preview(held)
    payload = json.loads(queue.get_reservation(key).conclusion_detail)
    payload["future_cleanup"] = {"state": state}
    raw = json.dumps(payload)
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET conclusion_detail=? WHERE key=?", (raw, key))
    before = asdict(queue.get_reservation(key))
    task_before = asdict(queue.get(task_id))
    with pytest.raises(TaskError, match="unknown cleanup component: future_cleanup"):
        apply(held, plan) if boundary == "apply" else preview(held)
    assert asdict(queue.get_reservation(key)) == before
    assert asdict(queue.get(task_id)) == task_before
    assert queue.cleanup_recovery_history(key) == []


def test_session_retry_with_producer_worktree_component_is_leased(held, monkeypatch):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, task_id, key = held
    payload = {
        "cleanup_kind": "attempt",
        "session_end": {"state": "held", "session_id": "native-session"},
        "worktree_cleanup": {"state": "complete", "outcome": {"action": "already-primed"}},
    }
    queue.record_spawn_conclusion(key, conclusion_state="held", conclusion_detail=json.dumps(payload))
    before = asdict(queue.get(task_id))
    apply(held, preview(held))
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(app)
        stages = []
        claim = client.claim_spawn_conclusion_retry
        validate = client.validate_spawn_conclusion_claim

        def claimed(exact_key, **kwargs):
            stages.append("claim")
            return claim(exact_key, **kwargs)

        def validated(exact_key, token, **kwargs):
            stages.append("validate")
            return validate(exact_key, token, **kwargs)

        monkeypatch.setattr(client, "claim_spawn_conclusion_retry", claimed)
        monkeypatch.setattr(client, "validate_spawn_conclusion_claim", validated)

        def end_session(session):
            stages.append("end")
            assert session == "native-session"
            token = queue.get_reservation(key).cleanup_claim_token
            assert token
            queue.validate_spawn_conclusion_claim(key, token, claimant_machine="host")
            return True

        def forbidden(*args, **kwargs):
            pytest.fail("completed worktree cleanup and business work must not replay")

        supervisor = Supervisor(
            client, spawn_fn=forbidden, local_end_fn=end_session,
            attempt_conclusion_fn=forbidden, repo=TEST_REPO, machine="host", nudge=False,
        )
        assert supervisor.release_requested_bodies() == 0
        assert stages == ["claim", "validate", "end"]
        assert queue.get_reservation(key).conclusion_state == "complete"
        assert supervisor.release_requested_bodies() == 0
        assert stages == ["claim", "validate", "end"]
        assert asdict(queue.get(task_id)) == before


@pytest.mark.parametrize("command", ["retry-cleanup", "cleanup-history"])
def test_cleanup_cli_errors_name_selected_command(monkeypatch, capsys, command):
    import types
    from contextlib import nullcontext
    from agent_dispatch import producers_cli
    from agent_dispatch.client import DispatchError

    def fail(*args, **kwargs):
        raise DispatchError(409, "fixture rejection")

    client = types.SimpleNamespace(recover_held_cleanup=fail, cleanup_recovery_history=fail)
    monkeypatch.setattr(producers_cli, "_client", lambda args: nullcontext(client))
    monkeypatch.setattr(producers_cli, "_registration_scope", lambda args: ("host", "default"))
    args = types.SimpleNamespace(
        reservations_command=command, key="exact-reservation", expected_generation=1,
        apply=False, expected_preview=None, reason=None, request_id=None,
    )
    assert producers_cli._cmd_reservations(args) == 2
    assert f"reservations {command}: HTTP 409" in capsys.readouterr().err


def test_recovery_supports_recorded_legacy_retry_progress(held):
    queue, _, key = held
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET conclusion_detail=? WHERE key=?",
            (json.dumps({"action": "preserved", "reason": "cleanup-retry-exhausted",
                         "attempts": 12, "acp_session_id": "session-exact"}), key),
        )
    apply(held, preview(held))
    payload = json.loads(queue.get_reservation(key).conclusion_detail)
    assert payload["attempts"] == 0
    assert payload["acp_session_id"] == "session-exact"
    assert "cleanup_kind" not in payload


def test_recovery_concurrent_lost_response_replay_is_one_transaction(held):
    from concurrent.futures import ThreadPoolExecutor
    queue, _, key = held
    plan = preview(held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply(held, plan), range(2)))
    assert results[0] == results[1]
    assert len(queue.cleanup_recovery_history(key)) == 1
    with pytest.raises(TaskError, match="request id"):
        queue.recover_held_cleanup(
            key, expected_generation=plan["generation"], current_host="host", apply=True,
            request_id="recovery-1", expected_preview=plan["preview_token"], reason="different",
        )


def test_recovery_interrupted_audit_rolls_back_eligibility(held, monkeypatch):
    queue, _, key = held
    plan = preview(held)

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit publication failure")

    monkeypatch.setattr(queue, "_audit", fail)
    with pytest.raises(RuntimeError, match="injected audit"):
        apply(held, plan)
    assert queue.get_reservation(key).conclusion_state == "held"
    assert queue.cleanup_recovery_history(key) == []


@pytest.mark.parametrize("field", ["reason", "request_id", "expected_preview"])
@pytest.mark.parametrize("value", [None, False, 0, True, 1, [], {}, ["value"], {"key": "value"}, "", "  "])
def test_direct_recovery_apply_rejects_invalid_request_identity_before_sql(held, monkeypatch, field, value):
    queue, task_id, key = held
    plan = preview(held)
    before = asdict(queue.get_reservation(key)), queue.events(task_id)
    arguments = {
        "expected_generation": plan["generation"], "current_host": "host",
        "apply": True, "reason": "corrected", "request_id": "exact-request",
        "expected_preview": plan["preview_token"],
    }
    arguments[field] = value

    def forbidden():
        raise AssertionError("invalid recovery identity reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(queue, "_connect", forbidden)
        with pytest.raises(TaskError):
            queue.recover_held_cleanup(key, **arguments)
    assert (asdict(queue.get_reservation(key)), queue.events(task_id)) == before
    assert queue.cleanup_recovery_history(key) == []


def test_recovery_history_survives_later_success(held):
    queue, _, key = held
    prior = queue.get_reservation(key).conclusion_detail
    apply(held, preview(held))
    _, _, token = queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
    queue.record_spawn_conclusion(
        key, conclusion_state="complete", conclusion_detail='{"action":"primed"}',
        claim_token=token, claimant_machine="host",
    )
    history = queue.cleanup_recovery_history(key)
    assert history[0]["prior_detail"] == prior
    assert history[0]["request"]["reason"] == "underlying fault corrected"


def test_recovery_rejects_stale_preview_and_unknown_lease(held):
    queue, _, key = held
    plan = preview(held)
    queue.record_spawn_conclusion(key, conclusion_state="held", conclusion_detail=json.dumps({
        "action": "preserved", "reason": "a different failure", "attempts": 12,
    }))
    with pytest.raises(TaskError, match="preview changed"):
        apply(held, plan)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET cleanup_claim_token='lease', "
            "cleanup_claim_expires_at=NULL WHERE key=?", (key,),
        )
    with pytest.raises(TaskError, match="expiry is unknown"):
        preview(held)


def test_recovery_rpc_requires_control_authority_and_strict_fences(held):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient, DispatchError
    from agent_dispatch.coordinator import create_app
    queue, task_id, key = held
    app = create_app(queue, control_token="fixture-control", enable_mcp=False)
    with DispatchClient("http://testserver") as ordinary:
        ordinary._http.close()
        ordinary._http = TestClient(app)
        with pytest.raises(DispatchError) as denied:
            ordinary.recover_held_cleanup(
                key, expected_generation=queue.get(task_id).generation, current_host="host",
            )
        assert denied.value.status_code == 403
    with DispatchClient("http://testserver", control_token="fixture-control") as control:
        control._http.close()
        control._http = TestClient(app)
        response = control._http.post(
            f"/spawn-reservations/{key}/conclusion/recover",
            json={"expected_generation": True, "current_host": "host"},
            headers=control._control_headers(),
        )
        assert response.status_code == 422
        result = control.recover_held_cleanup(
            key, expected_generation=queue.get(task_id).generation, current_host="host",
        )
        assert result["eligible"] is True
        assert control.cleanup_recovery_history(key) == []


@pytest.mark.parametrize("identity", ["{", "null", "[]", "true", "1", '"host"'])
@pytest.mark.parametrize("boundary", ["claim", "validate", "outcome"])
def test_recovery_rpc_corrupt_history_is_typed_and_preserved(held, identity, boundary):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    queue, task_id, key = held
    apply(held, preview(held))
    token = None
    if boundary != "claim":
        _, claimed, token = queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
        assert claimed
    with queue._connect() as conn:
        conn.execute("UPDATE cleanup_recoveries SET identity=?", (identity,))
    before = asdict(queue.get_reservation(key)), asdict(queue.get(task_id)), queue.events(task_id)
    body = {"claimant_machine": "host"}
    suffix = "/claim" if boundary == "claim" else "/validate" if boundary == "validate" else ""
    if boundary != "claim":
        body["claim_token"] = token
    if boundary == "outcome":
        body.update(conclusion_state="complete", conclusion_detail='{"action":"primed"}')
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/spawn-reservations/{key}/conclusion{suffix}", json=body)
    assert response.status_code == 409
    assert "recovery identity" in response.json()["detail"]
    assert (asdict(queue.get_reservation(key)), asdict(queue.get(task_id)), queue.events(task_id)) == before
    with queue._connect() as conn:
        assert conn.execute("SELECT identity FROM cleanup_recoveries").fetchone()["identity"] == identity


@pytest.mark.parametrize("field,value", [
    ("claimant_machine", 1), ("claimant_machine", True),
    ("claimant_machine", []), ("claimant_machine", {}),
    ("unexpected_fence", "host"),
])
@pytest.mark.parametrize("boundary", ["claim", "validate", "outcome"])
def test_cleanup_claim_rpc_rejects_invalid_bodies_before_queue(held, monkeypatch, field, value, boundary):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    queue, _, key = held
    before = asdict(queue.get_reservation(key))

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request reached cleanup queue")

    for method in ("claim_spawn_conclusion_retry", "validate_spawn_conclusion_claim",
                   "record_spawn_conclusion"):
        monkeypatch.setattr(queue, method, forbidden)
    body = {"claimant_machine": "host"}
    suffix = "/claim" if boundary == "claim" else "/validate" if boundary == "validate" else ""
    if boundary != "claim":
        body["claim_token"] = "exact-token"
    if boundary == "outcome":
        body.update(conclusion_state="complete", conclusion_detail='{"action":"primed"}')
    body[field] = value
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/spawn-reservations/{key}/conclusion{suffix}", json=body)
    assert response.status_code == 422
    assert asdict(queue.get_reservation(key)) == before


@pytest.mark.parametrize("value", [1, True, [], {}])
@pytest.mark.parametrize("boundary", ["validate", "outcome"])
def test_cleanup_claim_rpc_rejects_invalid_token(held, value, boundary):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    queue, _, key = held
    before = asdict(queue.get_reservation(key))
    body = {"claim_token": value, "claimant_machine": "host"}
    suffix = "/validate" if boundary == "validate" else ""
    if boundary == "outcome":
        body.update(conclusion_state="complete", conclusion_detail='{"action":"primed"}')
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/spawn-reservations/{key}/conclusion{suffix}", json=body)
    assert response.status_code == 422
    assert asdict(queue.get_reservation(key)) == before


def test_legacy_cleanup_claim_rpc_preserves_omitted_body(held):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    queue, _, key = held
    queue.record_spawn_conclusion(
        key, conclusion_state="pending", conclusion_detail='{"action":"pending"}',
    )
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with TestClient(app) as client:
        response = client.post(f"/spawn-reservations/{key}/conclusion/claim")
    assert response.status_code == 200
    assert response.json()["claimed"] is True
    assert response.json()["claim_token"]


@pytest.mark.parametrize("claimant", ["other-host", None, ""])
@pytest.mark.parametrize("boundary", ["claim", "validate", "outcome"])
def test_recovery_rpc_fences_claimant_host_at_every_boundary(held, claimant, boundary):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    queue, _, key = held
    apply(held, preview(held))
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with TestClient(app) as client:
        token = None
        if boundary != "claim":
            accepted = client.post(
                f"/spawn-reservations/{key}/conclusion/claim",
                json={"claimant_machine": "host"},
            )
            assert accepted.status_code == 200
            token = accepted.json()["claim_token"]
        before = asdict(queue.get_reservation(key))
        if boundary == "claim":
            response = client.post(
                f"/spawn-reservations/{key}/conclusion/claim",
                json={"claimant_machine": claimant},
            )
        elif boundary == "validate":
            response = client.post(
                f"/spawn-reservations/{key}/conclusion/validate",
                json={"claim_token": token, "claimant_machine": claimant},
            )
        else:
            response = client.post(
                f"/spawn-reservations/{key}/conclusion",
                json={"claim_token": token, "claimant_machine": claimant,
                      "conclusion_state": "complete", "conclusion_detail": '{"action":"primed"}'},
            )
        assert response.status_code == 409
        assert asdict(queue.get_reservation(key)) == before


@pytest.mark.parametrize("component", ["session", "worktree"])
def test_foreign_supervisor_cannot_consume_recovery_or_steal_lease(held, component):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, _, key = held
    if component == "session":
        payload = {
            "cleanup_kind": "attempt",
            "session_end": {"state": "held", "session_id": "native-session"},
            "worktree_cleanup": {"state": "complete", "outcome": {"action": "already-primed"}},
        }
        queue.record_spawn_conclusion(key, conclusion_state="held", conclusion_detail=json.dumps(payload))
    apply(held, preview(held))
    before = asdict(queue.get_reservation(key))
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(app)
        ended = []
        worktrees = []
        foreign = Supervisor(
            client, spawn_fn=lambda *a: pytest.fail("business spawn forbidden"),
            local_end_fn=lambda sid: ended.append(sid) or True,
            attempt_conclusion_fn=lambda *a: worktrees.append(a) or {"action": "primed"},
            repo=TEST_REPO, machine="other-host", nudge=False,
        )
        foreign.release_requested_bodies()
        assert ended == [] and worktrees == []
        assert asdict(queue.get_reservation(key)) == before
        local = Supervisor(
            client, spawn_fn=lambda *a: pytest.fail("business spawn forbidden"),
            local_end_fn=lambda sid: ended.append(sid) or True,
            attempt_conclusion_fn=lambda *a: worktrees.append(a) or {"action": "primed"},
            repo=TEST_REPO, machine="host", nudge=False,
        )
        local.release_requested_bodies()
        assert queue.get_reservation(key).conclusion_state == "complete"
        assert len(ended if component == "session" else worktrees) == 1


@pytest.mark.parametrize("claimant", ["host", "other-host", None])
def test_ordinary_bridge_identity_and_housekeeping_host_fence(held, monkeypatch, claimant):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.spawn_factories import make_headless_spawn, make_label_routed_spawn
    from agent_dispatch.supervisor import Supervisor
    from agent_dispatch import embody
    from agent_dispatch import remote_dispatch
    from tests._helpers import TEST_REPO

    queue, _, key = held
    apply(held, preview(held))
    before = asdict(queue.get_reservation(key))
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    rpc_hosts = []
    if claimant is None:
        monkeypatch.setattr(remote_dispatch, "local_machine", lambda: None)

    @app.middleware("http")
    async def observe_lease_host(request, call_next):
        if (request.method == "POST" and request.url.path.startswith("/spawn-reservations/")
                and request.url.path.rsplit("/", 1)[-1] in {"claim", "validate", "conclusion"}):
            body = json.loads(await request.body() or b"{}")
            rpc_hosts.append((request.url.path.rsplit("/", 1)[-1], body.get("claimant_machine")))
        return await call_next(request)

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary Bridge allocation must remain owned by the real spawn")

    monkeypatch.setattr(embody, "prepare_reusable_worktree", forbidden)
    routed = make_label_routed_spawn(lambda _: (False, {}), overrides={
        "review": make_headless_spawn(agent="local", charter="reviewer"),
    })
    worktrees = []
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(app)
        transport = client._http
        ordinary = client.create("ordinary Bridge fixture", repo=TEST_REPO)
        reservation, _ = queue.reserve_spawn(ordinary["id"])
        ordinary_before = asdict(queue.get(ordinary["id"]))
        supervisor = Supervisor(
            client, spawn_fn=routed, repo=TEST_REPO, machine=claimant, nudge=False,
            attempt_conclusion_fn=lambda *a: worktrees.append(a) or {
                "action": "primed", "managed_gc_eligible": True,
            },
        )
        snapshot = supervisor._prepare_spawn_task(
            {**ordinary, "labels": ["review"]}, {"key": reservation.key},
        )
        supervisor.release_requested_bodies()
        assert rpc_hosts and all(host == claimant for _, host in rpc_hosts)
        if claimant == "host":
            assert {"claim", "validate", "conclusion"} <= {stage for stage, _ in rpc_hosts}
            assert queue.get_reservation(key).conclusion_state == "complete"
            assert len(worktrees) == 1
        else:
            assert {stage for stage, _ in rpc_hosts} == {"claim"}
            assert asdict(queue.get_reservation(key)) == before
            assert worktrees == []
        assert asdict(queue.get(ordinary["id"])) == ordinary_before
        assert snapshot["_spawn_client"] is client
        assert supervisor.client is client
        assert "spawn_worktree" not in snapshot
    assert transport.is_closed


@pytest.mark.parametrize("claimant", [None, "host"])
@pytest.mark.parametrize("boundary", ["claim", "validate", "outcome"])
def test_conclusion_client_preserves_legacy_request_shape(claimant, boundary):
    import httpx
    from agent_dispatch.client import DispatchClient

    observed = []

    def respond(request):
        observed.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    with DispatchClient("http://fixture") as client:
        client._http.close()
        client._http = httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(respond))
        if boundary == "claim":
            client.claim_spawn_conclusion_retry("key", claimant_machine=claimant)
        elif boundary == "validate":
            client.validate_spawn_conclusion_claim("key", "token", claimant_machine=claimant)
        else:
            client.record_spawn_conclusion(
                "key", conclusion_state="complete", conclusion_detail="{}",
                claim_token="token", claimant_machine=claimant,
            )
    request = observed[0]
    if boundary == "claim" and claimant is None:
        assert request.content == b""
        assert "content-type" not in request.headers
    else:
        body = json.loads(request.content)
        if claimant is None:
            assert "claimant_machine" not in body
        else:
            assert body["claimant_machine"] == claimant
        if boundary != "claim":
            assert body["claim_token"] == "token"


@pytest.mark.parametrize("machine_arg,local_machine", [
    (None, "host"), ("host", "other-host"), (None, "other-host"), (None, None),
])
def test_actual_supervise_constructor_consumes_held_cleanup(held, monkeypatch, machine_arg, local_machine):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from agent_dispatch import __main__ as cli, remote_dispatch, supervisor as sup_mod
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from tests._helpers import TEST_REPO

    queue, _, key = held
    apply(held, preview(held))
    before = asdict(queue.get_reservation(key))
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    constructed = []
    worktrees = []
    real_supervisor = sup_mod.Supervisor

    def create_supervisor(*args, **kwargs):
        instance = real_supervisor(*args, **kwargs)
        constructed.append(instance)
        instance.attempt_conclusion_fn = lambda *a: worktrees.append(a) or {
            "action": "primed", "managed_gc_eligible": True,
        }
        instance.poll_once = lambda: (instance.release_requested_bodies(), [])[1]
        return instance

    def open_client(*args, **kwargs):
        client = DispatchClient("http://testserver")
        client._http.close()
        client._http = TestClient(app)
        return client

    monkeypatch.setattr(cli, "_client", open_client)
    monkeypatch.setattr(cli, "_scope_repo", lambda *a: TEST_REPO)
    monkeypatch.setattr(cli, "_spawn_route", lambda *a: None)
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: local_machine)
    monkeypatch.setattr(sup_mod, "Supervisor", create_supervisor)
    args = SimpleNamespace(
        all_repos=False, repo=TEST_REPO, url=None, token=None, label=None,
        max_concurrent=1, verify_timeout=0, once=True, interval=30,
        no_heartbeat=True, max_attempts=3, embody_backend="cli",
        cli_label=None, headless_label=None, machine=machine_arg,
    )
    assert cli._cmd_supervise(args) == 0
    expected_machine = machine_arg or local_machine
    if expected_machine == "host":
        assert queue.get_reservation(key).conclusion_state == "complete"
        assert len(worktrees) == 1
    else:
        assert asdict(queue.get_reservation(key)) == before
        assert worktrees == []
    assert constructed[0].machine == expected_machine


def test_default_constructor_identity_reaches_recovery_rpc(held, monkeypatch):
    from fastapi.testclient import TestClient
    from agent_dispatch import remote_dispatch
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, task_id, key = held
    task_before = asdict(queue.get(task_id))
    apply(held, preview(held))
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host")
    worktrees = []
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(create_app(queue, enable_mcp=False, verification_interval=0))
        supervisor = Supervisor(
            client, spawn_fn=lambda *a: pytest.fail("business spawn forbidden"),
            attempt_conclusion_fn=lambda *a: worktrees.append(a) or {"action": "primed"},
            repo=TEST_REPO, nudge=False,
        )
        assert supervisor.machine == "host"
        assert supervisor.client is client
        supervisor.release_requested_bodies()
    assert queue.get_reservation(key).conclusion_state == "complete"
    assert len(worktrees) == 1
    assert asdict(queue.get(task_id)) == task_before


def test_full_poll_preserves_session_component_before_legacy_reconcile(tmp_path):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    fixture = _held_cleanup(tmp_path, labels=["disposable"])
    queue, task_id, key = fixture
    task_before = asdict(queue.get(task_id))
    queue.record_spawn_conclusion(key, conclusion_state="held", conclusion_detail=json.dumps({
        "cleanup_kind": "terminal",
        "session_end": {"state": "held", "session_id": "native-session", "attempts": 12},
        "worktree_cleanup": {"state": "complete", "outcome": {"action": "primed"}},
    }))
    apply(fixture, preview(fixture))
    ended, worktrees = [], []
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(create_app(queue, enable_mcp=False, verification_interval=0))
        supervisor = Supervisor(
            client, spawn_fn=lambda *a: pytest.fail("business spawn forbidden"),
            local_end_fn=lambda sid: ended.append(sid) or True,
            conclusion_fn=lambda *a: worktrees.append(a) or {
                "action": "primed", "managed_gc_eligible": True,
            },
            repo=TEST_REPO, machine="host", disposable_cli_labels=["disposable"],
            heartbeat=False, nudge=False, recover=False, consistency_sweep=False,
        )
        assert supervisor.poll_once() == []
    assert ended == ["native-session"]
    assert worktrees == []
    row = queue.get_reservation(key)
    payload = json.loads(row.conclusion_detail)
    assert row.conclusion_state == "complete"
    assert payload["session_end"]["state"] == "complete"
    assert payload["worktree_cleanup"]["state"] == "complete"
    assert asdict(queue.get(task_id)) == task_before
    assert len(queue.cleanup_recovery_history(key)) == 1


def test_legacy_conclusion_rpc_without_recovery_keeps_optional_host_behavior(held):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    queue, _, _ = held
    task = queue.create("legacy pending cleanup")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.fail_spawn(reservation.key, conclusion_state="pending", conclusion_detail='{"action":"failed"}')
    app = create_app(queue, enable_mcp=False, verification_interval=0)
    with TestClient(app) as client:
        claim = client.post(f"/spawn-reservations/{reservation.key}/conclusion/claim")
        assert claim.status_code == 200
        token = claim.json()["claim_token"]
        assert client.post(
            f"/spawn-reservations/{reservation.key}/conclusion/validate", json={"claim_token": token},
        ).status_code == 200
        assert client.post(
            f"/spawn-reservations/{reservation.key}/conclusion",
            json={"claim_token": token, "conclusion_state": "complete",
                  "conclusion_detail": '{"action":"removed"}'},
        ).status_code == 200


@pytest.mark.parametrize("boundary", ["validate", "record"])
def test_legacy_cleanup_lease_rejects_equal_timestamp_competitor(held, boundary):
    queue, _, key = held
    queue.settle_spawn(key, conclusion_state="pending")
    _, claimed, token = queue.claim_spawn_conclusion_retry(key, now=11)
    assert claimed and token
    newer = queue.create("same timestamp allocation")
    competitor, _ = queue.reserve_spawn(newer.id, now=10)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree='worker-exact' WHERE key=?",
            (competitor.key,),
        )
    before = asdict(queue.get_reservation(key))
    with pytest.raises(TaskError, match="newer reservation"):
        if boundary == "validate":
            queue.validate_spawn_conclusion_claim(key, token, now=12)
        else:
            queue.record_spawn_conclusion(
                key, claim_token=token, conclusion_state="complete",
                conclusion_detail='{"action":"removed"}', now=12,
            )
    assert asdict(queue.get_reservation(key)) == before


@pytest.mark.parametrize("method", [
    "claim_spawn_conclusion_retry", "validate_spawn_conclusion_claim", "record_spawn_conclusion",
])
@pytest.mark.parametrize("claimant", [None, "host", "other-host"])
def test_host_bound_cleanup_client_validates_explicit_claimant(method, claimant):
    from agent_dispatch.client_cleanup_recovery import HostBoundConclusionClient

    calls = []

    class Client:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                return {"accepted": True}
            return call

    bound = HostBoundConclusionClient(Client(), "host")
    args = ("key", "token") if method == "validate_spawn_conclusion_claim" else ("key",)
    if claimant == "other-host":
        with pytest.raises(ValueError, match="bound"):
            getattr(bound, method)(*args, claimant_machine=claimant)
        assert calls == []
    else:
        assert getattr(bound, method)(*args, claimant_machine=claimant) == {"accepted": True}
        assert calls == [(method, args, {"claimant_machine": "host"})]


@pytest.mark.parametrize("operation", ["settle", "fail"])
def test_unbound_terminal_alias_cannot_publish_recovery_outcome(held, operation):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    queue, _, key = held
    apply(held, preview(held))
    _, _, token = queue.claim_spawn_conclusion_retry(key, claimant_machine="host")
    before = asdict(queue.get_reservation(key))
    with TestClient(create_app(queue, enable_mcp=False, verification_interval=0)) as client:
        result = client.post(
            f"/spawn-reservations/{key}/{operation}",
            json={"claim_token": token, "conclusion_state": "complete",
                  "conclusion_detail": '{"action":"primed"}'},
        )
        assert result.status_code == 409
    assert asdict(queue.get_reservation(key)) == before


@pytest.mark.parametrize("invalid_token", [None, "", " ", [], True, 0])
def test_supervisor_rejects_malformed_claim_token_before_validation(held, monkeypatch, caplog, invalid_token):
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, task_id, key = held
    task_before = asdict(queue.get(task_id))
    apply(held, preview(held))
    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(create_app(queue, enable_mcp=False, verification_interval=0))
        original = client.claim_spawn_conclusion_retry

        def malformed_claim(*args, **kwargs):
            return {**original(*args, **kwargs), "claim_token": invalid_token}

        monkeypatch.setattr(client, "claim_spawn_conclusion_retry", malformed_claim)
        monkeypatch.setattr(client, "validate_spawn_conclusion_claim", lambda *a, **k: pytest.fail(
            "a malformed token must not reach validation",
        ))
        supervisor = Supervisor(
            client, spawn_fn=lambda *a: pytest.fail("business work must not replay"),
            attempt_conclusion_fn=lambda *a: pytest.fail("malformed lease cannot authorize cleanup"),
            repo=TEST_REPO, machine="host", nudge=False,
        )
        assert supervisor.release_requested_bodies() == 0
    assert asdict(queue.get(task_id)) == task_before
    assert queue.get_reservation(key).conclusion_state == "pending"
    assert "cleanup claim protocol error" in caplog.text


@pytest.mark.parametrize("native_operation", ["preview", "apply", "replay", "history", "completed-replay"])
def test_native_cli_first_touch_preview_apply_and_history(held, native_operation):
    """Actual CLI -> loopback HTTP -> supervisor consumer -> fixture database."""
    import socket
    import os
    import subprocess
    import sys
    import threading
    import time

    from agent_procutil import no_window_kwargs
    import uvicorn
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import TEST_REPO

    queue, task_id, key = held
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET creating_host='fixture-host' WHERE key=?", (key,))
    before = asdict(queue.get(task_id))
    app = create_app(queue, control_token="fixture-control", enable_mcp=False, verification_interval=0)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        base = [
            sys.executable, "-m", "agent_dispatch", "--url",
            f"http://127.0.0.1:{sock.getsockname()[1]}", "--control-token", "fixture-control",
            "reservations",
        ]

        native_calls = []

        def run(*argv):
            native_calls.append(argv)
            result = subprocess.run(
                [*base, *argv], capture_output=True, text=True, timeout=15,
                env={**os.environ, "AGENT_DISPATCH_SUPERVISE_MACHINE": "fixture-host"},
                **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)

        args = ("retry-cleanup", key, "--expected-generation", str(before["generation"]))
        conclusions = []

        def conclude(*args):
            conclusions.append(args)
            return {"action": "primed", "managed_gc_eligible": True}

        def forbidden(*args, **kwargs):
            pytest.fail("housekeeping recovery must not spawn business work")

        with DispatchClient(
            f"http://127.0.0.1:{sock.getsockname()[1]}", control_token="fixture-control",
        ) as client:
            plan = run(*args) if native_operation == "preview" else client.recover_held_cleanup(
                key, expected_generation=before["generation"], current_host="fixture-host",
            )
            assert plan["scheduled"] is False
            apply_args = (
                *args, "--apply", "--expected-preview", plan["preview_token"],
                "--request-id", "native-request", "--reason", "fixture fault corrected",
            )

            def apply_http():
                return client.recover_held_cleanup(
                    key, expected_generation=before["generation"], current_host="fixture-host",
                    apply=True, expected_preview=plan["preview_token"],
                    request_id="native-request", reason="fixture fault corrected",
                )

            applied = run(*apply_args) if native_operation == "apply" else apply_http()
            assert applied["scheduled"] is True
            assert (run(*apply_args) if native_operation == "replay" else apply_http()) == applied
            history = run("cleanup-history", key) if native_operation == "history" else client.cleanup_recovery_history(key)
            assert history[0]["request_id"] == "native-request"
            supervisor = Supervisor(
                client, spawn_fn=forbidden, attempt_conclusion_fn=conclude,
                repo=TEST_REPO, machine="fixture-host", nudge=False,
            )
            assert supervisor.release_requested_bodies() == 0
            assert queue.get_reservation(key).conclusion_state == "complete"
            assert conclusions == [("worker-exact", "session-exact", key, "supervisor")]
            assert supervisor.release_requested_bodies() == 0
            assert len(conclusions) == 1
            assert (run(*apply_args) if native_operation == "completed-replay" else apply_http()) == applied
        assert len(native_calls) == 1
        assert queue.get_reservation(key).conclusion_state == "complete"
        assert asdict(queue.get(task_id)) == before
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()
