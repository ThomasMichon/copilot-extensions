"""Ordinary cold recovery retains real storage, routing and wake custody owners."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_dispatch.owner_wake_delivery import deliver_owner_wake
from agent_dispatch.queue import SpawnState, Status
from agent_dispatch.queue_pool_recovery import prepare_recovery_wake, reconcile_recoveries
from agent_dispatch.queue_task_recovery import _captured, _revision
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.task_wake_recovery import reconcile_wake
from tests import test_ordinary_pool_startup as controls
from tests._hosting_owner import admission_owner as admission_owner

q = controls.q
api = controls.api
ordinary = controls.ordinary


def recoverable(o: SimpleNamespace) -> tuple[str, str, int]:
    result = controls.launch(o)
    task_id, key = o.task["id"], o.placed["reservation"]["key"]
    o.client.record_spawn(
        key, session_handle=result["session"], worktree=result["worktree"],
        startup_complete=True, startup_revision=1, session_info=result["session_info"],
    )
    o.client.start_pool_task(
        task_id, "host-a/worktree-a", owner_session_id="native-a", expected_generation=1,
    )
    o.q.suspend(task_id, "host-a/worktree-a", reason="intentional wait", cooldown_seconds=None)
    o.q.record_cold(key)
    o.q.resume(task_id, "host-a/worktree-a", wake_requested=True, now=101)
    return task_id, key, o.q.pool_startup(task_id).recovery_revision


def test_supervisor_does_not_resume_or_accept_managed_cold_worker(ordinary: SimpleNamespace) -> None:
    o = ordinary
    task_id, key, _ = recoverable(o)
    supervisor = Supervisor(
        o.client, repo="example",
        spawn_fn=lambda *_args: pytest.fail("replacement allocation is forbidden"),
        local_resume_fn=lambda *_args: pytest.fail("legacy process resume is not worker acceptance"),
    )
    assert supervisor.release_resumed_cold_tasks(now=102) == 0
    assert o.q.get_reservation(key).state == SpawnState.COLD
    assert o.q.get(task_id).status == Status.SUSPENDED
    assert len(o.calls) == 2  # Original create and first send only.


def test_cold_wake_uses_retained_bridge_native_then_waits_for_worker(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    task_id, key, revision = recoverable(o)
    wake = o.q.claim_due_wake(now=102)
    prepare_recovery_wake(o.q, wake)
    sent = []

    def send(bridge: str, native: str, prompt: str, **kwargs: object) -> bool:
        sent.append((bridge, native, prompt, kwargs))
        return True

    monkeypatch.setattr("agent_dispatch.owner_wake_delivery.native_prompt", send)
    assert deliver_owner_wake(
        o.q, wake.owner, wake.owner_session_id, task_id, wake.message, wake.id,
    )
    assert sent[0][:2] == ("bridge-a", "native-a")
    assert f"--pool-recovery-revision {revision}" in sent[0][2]
    assert sent[0][3]["idempotency_key"] == wake.id
    o.q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=103)
    assert o.q.get(task_id).status == Status.SUSPENDED
    assert o.q.get_reservation(key).state == SpawnState.SPAWNED
    accepted = o.client.start_pool_task(
        task_id, "host-a/worktree-a", owner_session_id="native-a",
        expected_generation=1, recovery_revision=revision,
    )
    assert accepted["status"] == Status.STARTED
    assert len(o.allocations) == 1


def test_unqualified_recovery_unknown_uses_existing_original_receipt_reconciliation(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    task_id, _, recovery_revision = recoverable(o)
    wake = o.q.claim_due_wake(now=102)
    prepare_recovery_wake(o.q, wake)
    admission = o.q.admit_execution_wake(wake, provenance=None, now=102)
    o.q.settle_execution_wake(wake, admission, outcome="unknown", detail="original return unavailable")
    with o.q._connect() as conn:
        revision = _revision(_captured(o.q, conn, task_id))
    observations = []

    def observe(bridge: str, native: str, prompt: str, **kwargs: object) -> dict:
        observations.append((bridge, native, prompt, kwargs))
        return {
            "version": 1, "session_id": bridge, "native_session_id": native,
            "invocation_key": wake.id, "admission": "accepted", "queued": False, "turn_index": 1,
        }

    deadline = o.q.pool_startup(task_id).startup_deadline
    monkeypatch.setattr(o.q, "_now", lambda now: deadline - 1 if now is None else now)
    result = reconcile_wake(
        o.q, task_id, outbox="wake_outbox", wake_id=wake.id,
        expected_revision=revision, actor="operator", reason="observe original outcome", observe=observe,
    )
    assert result["outcome"] == "sent" and len(observations) == 1
    assert observations[0][:2] == ("bridge-a", "native-a")
    retained = o.q.list_wakes(task_id)[0]
    assert retained.status == "delivering" and retained.delivered_at == deadline - 1
    assert reconcile_recoveries(o.q, now=deadline) == 1
    assert o.q.get(task_id).status == Status.SUSPENDED
    assert o.q.pool_startup(task_id).decision_deadline == deadline - 1 + 1800
    monkeypatch.setattr(o.q, "_now", lambda now: deadline + 1 if now is None else now)
    accepted = o.client.start_pool_task(
        task_id, "host-a/worktree-a", owner_session_id="native-a",
        expected_generation=1, recovery_revision=recovery_revision,
    )
    assert accepted["status"] == Status.STARTED
    assert o.q.finish_wake(
        wake.id, wake.delivery_token, delivered=False, now=deadline + 2,
    ).status == "stale"
    assert o.q.get(task_id).status == Status.STARTED
    assert len(o.allocations) == 1
