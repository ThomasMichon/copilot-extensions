"""Held housekeeping recovery never reopens the business task."""
from dataclasses import asdict
import json

import pytest

from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def held(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("finished work", exclusive_key="logical-work")
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
    _, claimed, token = queue.claim_spawn_conclusion_retry(key)
    assert claimed and token
    queue.validate_spawn_conclusion_claim(key, token)
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
def test_recovery_newer_allocation_is_held_once_by_real_supervisor(held, monkeypatch, settled):
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
    reservation, _ = queue.reserve_spawn(newer.id, now=20)
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

        def claim(exact_key):
            claims.append(exact_key)
            return original(exact_key)

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
    _, claimed, token = queue.claim_spawn_conclusion_retry(key)
    assert claimed
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (task_id,))
    with pytest.raises(TaskError, match="generation"):
        queue.validate_spawn_conclusion_claim(key, token)


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
        _, claimed, token = queue.claim_spawn_conclusion_retry(key)
        assert claimed
    with queue._connect() as conn:
        conn.execute(f"UPDATE spawn_reservations SET {column}=? WHERE key=?", (value, key))
    detail = queue.get_reservation(key).conclusion_detail
    with pytest.raises(TaskError, match="provenance changed"):
        if boundary == "claim":
            queue.claim_spawn_conclusion_retry(key)
        elif boundary == "validate":
            queue.validate_spawn_conclusion_claim(key, token)
        else:
            queue.record_spawn_conclusion(
                key, conclusion_state="complete", conclusion_detail='{"action":"primed"}',
                claim_token=token,
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


def test_recovery_history_survives_later_success(held):
    queue, _, key = held
    prior = queue.get_reservation(key).conclusion_detail
    apply(held, preview(held))
    _, _, token = queue.claim_spawn_conclusion_retry(key)
    queue.record_spawn_conclusion(
        key, conclusion_state="complete", conclusion_detail='{"action":"primed"}', claim_token=token,
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


def test_native_cli_first_touch_preview_apply_and_history(held):
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

        def run(*argv):
            result = subprocess.run(
                [*base, *argv], capture_output=True, text=True, timeout=15,
                env={**os.environ, "AGENT_DISPATCH_SUPERVISE_MACHINE": "fixture-host"},
                **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)

        args = ("retry-cleanup", key, "--expected-generation", str(before["generation"]))
        plan = run(*args)
        assert plan["scheduled"] is False
        applied = run(
            *args, "--apply", "--expected-preview", plan["preview_token"],
            "--request-id", "native-request", "--reason", "fixture fault corrected",
        )
        assert applied["scheduled"] is True
        assert run(
            *args, "--apply", "--expected-preview", plan["preview_token"],
            "--request-id", "native-request", "--reason", "fixture fault corrected",
        ) == applied
        assert run("cleanup-history", key)[0]["request_id"] == "native-request"
        conclusions = []

        def conclude(*args):
            conclusions.append(args)
            return {"action": "primed", "managed_gc_eligible": True}

        def forbidden(*args, **kwargs):
            pytest.fail("housekeeping recovery must not spawn business work")

        with DispatchClient(
            f"http://127.0.0.1:{sock.getsockname()[1]}", control_token="fixture-control",
        ) as client:
            supervisor = Supervisor(
                client, spawn_fn=forbidden, attempt_conclusion_fn=conclude,
                repo=TEST_REPO, machine="fixture-host", nudge=False,
            )
            assert supervisor.release_requested_bodies() == 0
            assert queue.get_reservation(key).conclusion_state == "complete"
            assert conclusions == [("worker-exact", "session-exact", key, "supervisor")]
            assert supervisor.release_requested_bodies() == 0
            assert len(conclusions) == 1
        assert run(
            *args, "--apply", "--expected-preview", plan["preview_token"],
            "--request-id", "native-request", "--reason", "fixture fault corrected",
        ) == applied
        assert queue.get_reservation(key).conclusion_state == "complete"
        assert asdict(queue.get(task_id)) == before
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()
