"""Actual supervisor decisions over the negotiated API and durable queue."""

from __future__ import annotations

from dataclasses import asdict

import httpx
import pytest

from agent_dispatch import bridge, pool_startup_delivery, supervisor_pool_startup as startup
from agent_dispatch.queue import SpawnState, Status, TaskQueue
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, record_native, task

q = controls.q
api = controls.api


def setup(q, api, monkeypatch, *, native=True, complete=True, rewrite=None, startup_timeout=600):
    clock = [100.0]
    monkeypatch.setattr(q, "_now", lambda now=None: clock[0] if now is None else now)
    revision = publish(q, pool())
    requested = task(q)
    with controls.connected(api, []) as allocator:
        placed = allocator.place_pool_startup(
            requested.id, reserved_by="allocator", catalog_revision=revision,
            startup_timeout=startup_timeout,
        )
    reservation = q.get_reservation(placed["reservation"]["key"])
    if native:
        record_native(q, reservation)
        if complete:
            q.record_spawn(
                reservation.key, session_handle="native", startup_complete=True, startup_revision=1,
            )
    probes, nudges, requests = [], [], []

    def verdict(worktree, machine, native):
        probes.append(("verdict", worktree, machine, native))
        return "live"

    def live(worktree, machine):
        probes.append(("live", worktree, machine))
        return {"session_id": "native", "worktree_id": "worktree"}

    def nudge(worktree, message, **kwargs):
        nudges.append((worktree, message, kwargs))
        return True

    monkeypatch.setattr(bridge, "send_nudge", nudge)
    monkeypatch.setattr(
        pool_startup_delivery, "deliver_retry",
        lambda *args, **kwargs: ("startup_unknown", "contained unproven delivery"),
    )
    client = controls.connected(api, requests, rewrite=rewrite)
    owner = Supervisor(
        client, repo="example", machine="host-a", supervisor_id="allocator",
        spawn_fn=lambda _: pytest.fail("startup reconciliation must not spawn"),
        liveness_fn=live, verdict_fn=verdict,
        redrive_fn=lambda *args: pytest.fail("allocator startup must not use legacy prompt"),
        worktree_directory_present_fn=lambda *args: pytest.fail("unknown native is not workspace absence"),
    )
    return owner, clock, requested, reservation, probes, nudges, requests


def cycle(owner, clock, now):
    clock[0] = now
    return owner.reconcile_reserving(now=now) + owner.redrive_unclaimed_spawns(now=now)


def test_reachable_and_due_notify_use_exact_native_without_acceptance(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    with owner.client:
        assert cycle(owner, clock, 101) == 1
        initial = q.pool_startup(t.id)
        assert initial.reachable_at == 101 and initial.decision_deadline == 1901
        assert initial.next_nudge_at == 401 and initial.startup_attempts == 1
        assert cycle(owner, clock, 400) == 0 and not nudges
        assert cycle(owner, clock, 401) == 1
        assert cycle(owner, clock, 401) == 0
    assert all(probe[1:3] == ("worktree", "host-a") for probe in probes)
    assert probes[0] == ("verdict", "worktree", "host-a", "native")
    worktree, message, kwargs = nudges[0]
    assert worktree == "worktree" and "--evaluation" not in message
    assert kwargs["machine"] == "host-a" and kwargs["expected_session_id"] == "native"
    assert kwargs["idempotency_key"] == f"pool-startup:{r.key}:1:1:701.0"
    current = q.pool_startup(t.id)
    assert current.reachable_at == 101 and current.decision_deadline == 1901
    assert current.startup_attempts == 1 and current.cooldown_stage == 0
    assert q.get(t.id).status == Status.CLAIMED and q.get(t.id).owner is None
    assert any(str(request.url).endswith("/nudge") for request in requests)


def test_restart_preserves_nudge_and_decision_deadlines(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    with owner.client:
        cycle(owner, clock, 101)
        cycle(owner, clock, 401)
        reopened = TaskQueue(q.db_path)
        assert reopened.pool_startup(t.id) == q.pool_startup(t.id)
        replacement = Supervisor(
            owner.client, repo="example", liveness_fn=owner.liveness_fn,
            verdict_fn=owner.verdict_fn, spawn_fn=lambda _: pytest.fail("no replacement spawn"),
            supervisor_id="allocator", machine="host-a",
        )
        assert cycle(replacement, clock, 402) == 0
        assert cycle(replacement, clock, 701) == 1
        assert cycle(replacement, clock, 1901) == 1
    assert len(nudges) == 2
    assert q.get(t.id).status == Status.QUEUED
    assert q.get(t.id).not_before == 5501
    assert q.pool_startup(t.id).exhaustion_reason == "decision_timeout"
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING and retained.session_handle == "native"
    assert retained.release_requested and retained.startup_fenced


@pytest.mark.parametrize("probe", [
    None, {}, {"session_id": "other", "worktree_id": "worktree"},
    {"session_id": "native", "worktree_id": "other"},
    {"session_id": "native"}, {"worktree_id": "worktree"},
])
def test_missing_or_different_probe_never_becomes_reachability(q, api, monkeypatch, probe):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    owner.liveness_fn = lambda *args: probe
    with owner.client:
        assert cycle(owner, clock, 101) == 0
        assert q.pool_startup(t.id).reachable_at is None
        assert cycle(owner, clock, 700) == 1
    assert not nudges and q.get(t.id).status == Status.QUEUED
    assert q.get_reservation(r.key).state == SpawnState.RELEASING


@pytest.mark.parametrize("verdict", ["gone", "unknown"])
def test_gone_or_unknown_managed_startup_cannot_use_legacy_retirement(q, api, monkeypatch, verdict):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    owner.verdict_fn = lambda *args: verdict
    with owner.client:
        assert cycle(owner, clock, 101) == 0
        assert owner.recover_gone() == 0
    assert q.get_reservation(r.key).state == SpawnState.SPAWNED
    assert q.get(t.id).status == Status.CLAIMED
    assert not nudges and not probes


@pytest.mark.parametrize("native,complete", [(False, False), (True, False)])
def test_incomplete_initial_caller_exhausts_without_marking_it_finished(q, api, monkeypatch, native, complete):
    owner, clock, t, r, probes, nudges, requests = setup(
        q, api, monkeypatch, native=native, complete=complete,
    )
    with owner.client:
        assert cycle(owner, clock, 101) == 0
        assert cycle(owner, clock, 700) == 1
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING
    assert not retained.startup_complete and not retained.startup_fenced
    assert not nudges and not probes and q.get(t.id).status == Status.QUEUED


@pytest.mark.parametrize("kind", ["startup_failed", "startup_rejected", "startup_unknown"])
def test_live_failed_caller_requires_retry_not_reclassification(q, api, monkeypatch, kind):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch, complete=False)
    q.record_pool_startup_outcome(
        r.key, task_id=t.id, generation=1, startup_revision=1,
        native_id="native", outcome_kind=kind, detail="original return",
    )
    with owner.client:
        assert cycle(owner, clock, 101) == 0
    assert bool(probes) is (kind != "startup_unknown")
    assert not nudges
    assert q.pool_startup(t.id).reachable_at is None
    assert q.pool_startup(t.id).outcome_kind == "startup_unknown"
    assert q.pool_startup(t.id).startup_attempts == (1 if kind == "startup_unknown" else 2)


def test_three_actual_failed_attempts_exhaust_without_a_fourth_invocation(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch, complete=False)
    for revision in (1, 2, 3):
        q.record_pool_startup_outcome(
            r.key, task_id=t.id, generation=1, startup_revision=revision,
            native_id="native", outcome_kind="startup_failed", detail=f"return {revision}",
        )
        if revision != 3:
            q.record_spawn(
                r.key, session_handle="native", startup_complete=False, startup_revision=revision,
            )
    with owner.client:
        assert cycle(owner, clock, 101) == 1
    assert not probes and not nudges
    assert q.pool_startup(t.id).startup_attempts == 3
    assert q.pool_startup(t.id).exhaustion_reason == "startup_attempts_exhausted"
    assert q.get_reservation(r.key).state == SpawnState.RELEASING


@pytest.mark.parametrize("held", ["admission", "governance", "task"])
def test_holds_block_supervision_and_exhaustion(q, api, monkeypatch, held):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    if held == "admission":
        monkeypatch.setattr(startup, "action_inhibited", lambda *args, **kwargs: True)
    elif held == "governance":
        owner._recheck_governance = lambda *args: {"status": "held"}
    else:
        q.set_hold(t.id, reason="operator hold", actor="operator")
    before = q.pool_startup(t.id), q.get_reservation(r.key)
    with owner.client:
        assert cycle(owner, clock, 700) == 0
    assert (q.pool_startup(t.id), q.get_reservation(r.key)) == before
    assert not probes and not nudges


def test_lost_nudge_response_never_resends_same_slot_or_accepts(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    monkeypatch.setattr(bridge, "send_nudge", lambda *args, **kwargs: False)
    with owner.client:
        cycle(owner, clock, 101)
        assert cycle(owner, clock, 401) == 0
        after = q.pool_startup(t.id)
        assert after.next_nudge_at == 701
        assert cycle(owner, clock, 402) == 0
    assert q.pool_startup(t.id) == after and q.get(t.id).status == Status.CLAIMED


def test_consumer_uses_persisted_timeout_not_restarted_supervisor_settings(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(
        q, api, monkeypatch, native=False, complete=False, startup_timeout=42,
    )
    owner.reserving_timeout = 1
    with owner.client:
        assert cycle(owner, clock, 141) == 0
        assert cycle(owner, clock, 142) == 1
    assert q.pool_startup(t.id).startup_timeout == 42
    assert q.pool_startup(t.id).exhaustion_reason == "startup_timeout"
    assert q.get(t.id).not_before == 3742 and not probes and not nudges


def test_wrong_allocator_retains_claim_without_probing_or_mutating(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    owner.supervisor_id = "different-lane"
    before = q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)
    with owner.client:
        assert cycle(owner, clock, 700) == 0
    assert (q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)) == before
    assert not probes and not nudges


def test_probe_exception_is_unknown_until_bounded_exhaustion(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)

    def fail(*args):
        raise RuntimeError("probe unavailable")

    owner.verdict_fn = fail
    with owner.client:
        assert cycle(owner, clock, 101) == 0
        assert q.pool_startup(t.id).reachable_at is None
        assert cycle(owner, clock, 700) == 1
    assert q.get_reservation(r.key).state == SpawnState.RELEASING and not nudges


@pytest.mark.parametrize("action", ["reachable", "nudge", "exhaust"])
def test_lost_committed_decision_reply_is_not_replayed(q, api, monkeypatch, action):
    lose = [False]

    def rewrite(request, response):
        if lose[0] and request.url.path.endswith(f"/{action}"):
            raise httpx.ReadError("lost committed decision response")
        return response

    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch, rewrite=rewrite)
    with owner.client:
        if action != "reachable":
            cycle(owner, clock, 101)
        lose[0] = True
        at = {"reachable": 101, "nudge": 401, "exhaust": 1901}[action]
        assert cycle(owner, clock, at) == 0
        after = q.pool_startup(t.id)
        assert cycle(owner, clock, at + 1) == 0
    assert q.pool_startup(t.id) == after and not nudges
    assert sum(request.url.path.endswith(f"/{action}") for request in requests) == 1
    if action == "exhaust":
        assert q.get(t.id).status == Status.QUEUED
        assert q.get_reservation(r.key).state == SpawnState.RELEASING
    else:
        assert q.get(t.id).status == Status.CLAIMED


@pytest.mark.parametrize("failure", ["rollback", "transport", "malformed"])
def test_unknown_provider_never_falls_through_to_legacy_gone_recovery(q, api, monkeypatch, failure):
    def rewrite(request, response):
        if request.url.path.endswith("/contract"):
            if failure == "rollback":
                return httpx.Response(404, json={"detail": "old provider"})
            if failure == "transport":
                raise httpx.ReadError("lost response")
            return httpx.Response(200, json={"supported_versions": [1]})
        return response

    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch, rewrite=rewrite)
    owner.verdict_fn = lambda *args: "gone"
    before = q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)
    with owner.client:
        assert cycle(owner, clock, 700) == 0
        assert owner.recover_gone() == 0
    assert (q.pool_startup(t.id), q.get_reservation(r.key), q.get(t.id)) == before
    assert not probes and not nudges


def test_stale_snapshot_or_explicit_no_assignment_preserves_correct_boundary(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests = setup(q, api, monkeypatch)
    snapshot = q.pool_startup_snapshot(t.id)
    snapshot["reservation"]["startup_revision"] += 1
    owner.client.pool_startup_snapshot = lambda _: snapshot
    assert startup.supervise_pool_startup(owner, asdict(q.get_reservation(r.key)), asdict(q.get(t.id))) is False
    owner.client.pool_startup_snapshot = lambda _: None
    assert startup.supervise_pool_startup(owner, asdict(q.get_reservation(r.key)), asdict(q.get(t.id))) is None
    assert not probes and not nudges
    owner.client.close()


@pytest.mark.parametrize("status,owner_id,driver", [
    ("suspended", None, "cli"), ("started", "worker", "cli"),
    ("claimed", "worker", "cli"), ("claimed", None, "ordinary-bridge"),
    ("claimed", None, "restricted-bridge"), ("queued", None, "cli"),
])
def test_non_startup_paths_do_not_require_new_provider(status, owner_id, driver):
    from types import SimpleNamespace

    owner = SimpleNamespace(client=SimpleNamespace(
        pool_startup_snapshot=lambda _: pytest.fail("legacy path must not negotiate"),
    ))
    assert startup.supervise_pool_startup(
        owner, {"driver": driver}, {"status": status, "owner": owner_id},
    ) is None


def test_exact_notification_uses_existing_transport_without_new_work(monkeypatch):
    from agent_dispatch import bridge_command_delivery

    calls = []
    monkeypatch.setattr(bridge_command_delivery, "send_command",
                        lambda argv, prompt, **kwargs: calls.append((argv, prompt, kwargs)) or True)
    assert bridge.send_nudge(
        "worktree", "decision reminder", machine="host-a", expected_session_id="native",
        idempotency_key="slot",
    )
    argv, prompt, arguments = calls[0]
    assert arguments["machine"] == "host-a" and prompt == "decision reminder"
    assert argv[argv.index("--expected-session-id") + 1] == "native"
    assert argv[argv.index("--kind") + 1] == "notify"
    assert "--queue" not in argv and "--new" not in argv and "--force" not in argv
    assert argv[-3:] == ["worktree", "--prompt-file", "-"]
    with pytest.raises(ValueError, match="exact retained"):
        bridge.send_nudge("worktree", "reminder", machine="host-a")
