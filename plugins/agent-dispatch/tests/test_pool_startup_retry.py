"""Real transaction/HTTP controls for exact same-native startup retry custody."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from agent_dispatch.client import DispatchError, DispatchUpgradeRequired
from agent_dispatch.queue import SpawnState, Status, TaskError, TaskQueue
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, record_native, task

q = controls.q
api = controls.api


def failed(q, api, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(q, "_now", lambda now=None: clock[0] if now is None else now)
    revision = publish(q, pool())
    t = task(q)
    with controls.connected(api, []) as client:
        placed = client.place_pool_startup(
            t.id, reserved_by="allocator", catalog_revision=revision, startup_timeout=42,
        )
    r = q.get_reservation(placed["reservation"]["key"])
    record_native(q, r)
    q.record_pool_startup_outcome(
        r.key, task_id=t.id, generation=1, startup_revision=1, native_id="native",
        outcome_kind="startup_failed", detail="original completed caller",
    )
    return q.get(t.id), q.get_reservation(r.key), clock


def arguments(t, revision=1):
    return dict(
        generation=t.generation, startup_revision=revision, native_id="native",
        worktree="worktree", machine="host-a", reserved_by="allocator",
    )


def complete(client, t, r, kind="startup_succeeded", detail="original completed retry"):
    return client.complete_pool_startup(
        t.id, r.key, **arguments(t, 2), outcome_kind=kind, detail=detail,
    )


def test_one_exact_retry_and_completion_preserve_identity_capacity_and_clocks(q, api, monkeypatch):
    t, r, clock = failed(q, api, monkeypatch)
    clock[0] = 110
    with controls.connected(api, []) as client:
        pending = client.retry_pool_startup(t.id, r.key, **arguments(t))
        assert pending["reservation"]["startup_revision"] == 2
        assert pending["reservation"]["startup_complete"] is False
        assert "--pool-generation 1" in pending["invocation"]["prompt"]
        assert pending["invocation"]["idempotency_key"] == f"pool-startup:{r.key}:1:2:retry"
        budget = q.pool_startup(t.id)
        assert budget.startup_attempts == 2 and budget.startup_deadline == 152
        assert budget.retry_invocation == pending["invocation"]
        assert TaskQueue(q.db_path).pool_startup(t.id) == budget
        complete(client, t, r)
        assert complete(client, t, r)["reservation"]["startup_complete"]
    current = q.get_reservation(r.key)
    assert current.session_handle == r.session_handle and current.worktree == r.worktree
    assert q.pool_startup(t.id).startup_attempts == 2 and q.pool_startup(t.id).reachable_at is None
    assert q.get(t.id).status == Status.CLAIMED and q.get(t.id).owner is None
    assert len(q.list_reservations(task_id=t.id)) == 1 and not current.exclusive_released
    with pytest.raises(TaskError, match="capacity"):
        q.reserve_spawn(task(q).id, driver="cli", reserved_by="allocator",
                        pool_catalog_revision=q.pool_catalog_snapshot().revision)


@pytest.mark.parametrize("field,value", [
    ("generation", 2), ("startup_revision", 2), ("native_id", "other"),
    ("worktree", "other"), ("machine", "other"), ("reserved_by", "other"),
])
@pytest.mark.parametrize("operation", ["retry", "complete"])
def test_every_incarnation_and_caller_fence_is_inside_actual_write(
    q, api, monkeypatch, field, value, operation,
):
    t, r, clock = failed(q, api, monkeypatch)
    with controls.connected(api, []) as client:
        if operation == "complete":
            client.retry_pool_startup(t.id, r.key, **arguments(t))
        before = q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)
        kwargs = arguments(t, 2 if operation == "complete" else 1)
        kwargs[field] = value if field != "startup_revision" or operation == "retry" else 3
        if operation == "complete":
            kwargs.update(outcome_kind="startup_succeeded", detail="return")
        with pytest.raises(DispatchError) as failure:
            getattr(client, f"{operation}_pool_startup")(t.id, r.key, **kwargs)
        assert failure.value.status_code == 409
    assert (q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)) == before


@pytest.mark.parametrize("condition", ["unknown", "successful", "held", "expired", "wrong-task"])
def test_retry_requires_completed_failed_current_unheld_caller(q, api, monkeypatch, condition):
    t, r, clock = failed(q, api, monkeypatch)
    if condition in {"unknown", "successful"}:
        with q._connect() as conn:
            conn.execute(
                "UPDATE task_pool_assignments SET outcome_kind=? WHERE task_id=?",
                ("startup_unknown" if condition == "unknown" else None, t.id),
            )
    elif condition == "held":
        q.set_hold(t.id, reason="operator", actor="operator")
    elif condition == "expired":
        clock[0] = 142
    before = q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)
    with controls.connected(api, []) as client, pytest.raises(DispatchError):
        client.retry_pool_startup(
            task(q).id if condition == "wrong-task" else t.id, r.key, **arguments(t),
        )
    assert (q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)) == before


def test_unknown_caller_blocks_legacy_and_new_invocations_but_can_reconcile(q, api, monkeypatch):
    t, r, clock = failed(q, api, monkeypatch)
    with controls.connected(api, []) as client:
        client.retry_pool_startup(t.id, r.key, **arguments(t))
        complete(client, t, r, "startup_unknown", "lost transport")
        before = q.pool_startup(t.id), q.get_reservation(r.key)
        with pytest.raises(DispatchError):
            client.retry_pool_startup(t.id, r.key, **arguments(t, 2))
        with pytest.raises(TaskError, match="negotiated"):
            q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=2)
        with pytest.raises(TaskError, match="negotiated"):
            q.record_pool_startup_outcome(
                r.key, task_id=t.id, generation=1, startup_revision=2, native_id="native",
                outcome_kind="startup_failed", detail="unbound return",
            )
        assert (q.pool_startup(t.id), q.get_reservation(r.key)) == before
        complete(client, t, r)
    assert q.get_reservation(r.key).startup_complete and q.pool_startup(t.id).startup_attempts == 2


def test_late_original_retry_completion_preserves_exhausted_generation_and_capacity(q, api, monkeypatch):
    t, r, clock = failed(q, api, monkeypatch)
    with controls.connected(api, []) as client:
        client.retry_pool_startup(t.id, r.key, **arguments(t))
        clock[0] = 142
        client.exhaust_pool_startup(t.id, r.key, generation=1, startup_revision=2)
        before = q.get(t.id), q.pool_startup(t.id).cooldown_stage
        complete(client, t, r)
    current = q.get_reservation(r.key)
    assert current.state == SpawnState.RELEASING and current.release_requested
    assert current.startup_complete and not current.exclusive_released
    assert (q.get(t.id), q.pool_startup(t.id).cooldown_stage) == before


def test_concurrent_exact_retry_charges_only_one_original_invocation(q, api, monkeypatch):
    t, r, clock = failed(q, api, monkeypatch)
    barrier = Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        with controls.connected(api, []) as client:
            try:
                client.retry_pool_startup(t.id, r.key, **arguments(t))
                return True
            except DispatchError:
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: reserve(), range(2))) == [False, True]
    assert q.pool_startup(t.id).startup_attempts == 2
    assert q.get_reservation(r.key).startup_revision == 2


def test_provider_admission_hold_refuses_retry_but_not_original_completion(q, api, monkeypatch):
    t, r, clock = failed(q, api, monkeypatch)

    def held():
        raise TaskError("provider admission held")

    with controls.connected(api, []) as client:
        monkeypatch.setattr(q, "require_worker_admission", held)
        before = q.pool_startup(t.id), q.get_reservation(r.key)
        with pytest.raises(DispatchError) as failure:
            client.retry_pool_startup(t.id, r.key, **arguments(t))
        assert failure.value.status_code == 409
        assert (q.pool_startup(t.id), q.get_reservation(r.key)) == before
        monkeypatch.setattr(q, "require_worker_admission", lambda: None)
        client.retry_pool_startup(t.id, r.key, **arguments(t))
        monkeypatch.setattr(q, "require_worker_admission", held)
        complete(client, t, r)
    assert q.get_reservation(r.key).startup_complete


@pytest.mark.parametrize("action", ["retry", "complete"])
def test_rollback_and_lost_response_do_not_use_legacy_or_repeat_effect(q, api, monkeypatch, action):
    t, r, clock = failed(q, api, monkeypatch)
    if action == "complete":
        with controls.connected(api, []) as client:
            client.retry_pool_startup(t.id, r.key, **arguments(t))
    requests = []

    def rewrite(request, response):
        if request.url.path.endswith(f"/{action}"):
            raise httpx.ReadError("response lost", request=request)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client, pytest.raises(httpx.ReadError):
        if action == "retry":
            client.retry_pool_startup(t.id, r.key, **arguments(t))
        else:
            complete(client, t, r)
    assert sum(request.url.path.endswith(f"/{action}") for request in requests) == 1
    assert q.get_reservation(r.key).startup_revision == 2
    assert q.get_reservation(r.key).startup_complete is (action == "complete")

    def old(request):
        return httpx.Response(404, json={"detail": "old runtime"})

    from agent_dispatch.client import DispatchClient
    with DispatchClient("http://test", control_token="control", transport=httpx.MockTransport(old)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.retry_pool_startup(t.id, r.key, **arguments(t, 2))
