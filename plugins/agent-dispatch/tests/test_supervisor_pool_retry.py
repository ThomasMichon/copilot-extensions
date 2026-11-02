"""Actual negotiated supervisor retry, completion loss and late-caller recovery."""

import httpx
import pytest

from agent_dispatch import pool_startup_delivery
from agent_dispatch.queue import SpawnState, Status
from tests import test_supervisor_pool_startup as controls

q = controls.q
api = controls.api


def setup(q, api, monkeypatch, rewrite=None):
    owner, clock, t, r, probes, nudges, requests = controls.setup(
        q, api, monkeypatch, complete=False, rewrite=rewrite,
    )
    q.record_pool_startup_outcome(
        r.key, task_id=t.id, generation=1, startup_revision=1, native_id="native",
        outcome_kind="startup_failed", detail="original completed failure",
    )
    sends = []

    def deliver(invocation, *, observe_only):
        sends.append((invocation, observe_only))
        return "startup_succeeded", "exact admission observed"

    monkeypatch.setattr(pool_startup_delivery, "deliver_retry", deliver)
    return owner, clock, t, r, probes, nudges, requests, sends


def test_actual_supervisor_retries_same_native_before_reachability_and_never_accepts(q, api, monkeypatch):
    owner, clock, t, r, probes, nudges, requests, sends = setup(q, api, monkeypatch)
    with owner.client:
        assert controls.cycle(owner, clock, 101) == 1
        assert controls.cycle(owner, clock, 102) == 1
    assert len(sends) == 1 and sends[0][1] is False
    assert sends[0][0]["native_id"] == "native" and sends[0][0]["startup_revision"] == 2
    assert q.get_reservation(r.key).session_handle == "native"
    assert q.pool_startup(t.id).startup_attempts == 2
    assert q.pool_startup(t.id).reachable_at == 102 and q.get(t.id).status == Status.CLAIMED
    assert not nudges and q.get(t.id).owner is None


@pytest.mark.parametrize("lost", ["retry", "complete"])
def test_committed_response_loss_never_sends_again_and_reconciles_only_receipt(
    q, api, monkeypatch, lost,
):
    failures = []

    def rewrite(request, response):
        if request.url.path.endswith(f"/{lost}") and not failures:
            failures.append(request)
            raise httpx.ReadError("committed response lost", request=request)
        return response

    owner, clock, t, r, probes, nudges, requests, sends = setup(q, api, monkeypatch, rewrite)
    with owner.client:
        assert controls.cycle(owner, clock, 101) == 0
        if lost == "retry":
            assert not sends and not q.get_reservation(r.key).startup_complete
            monkeypatch.setattr(pool_startup_delivery, "deliver_retry", lambda invocation, **kwargs:
                                sends.append((invocation, kwargs["observe_only"])) or
                                ("startup_unknown", "original send was not observed"))
            assert controls.cycle(owner, clock, 102) == 0
            assert all(observe_only for _, observe_only in sends)
        else:
            assert len(sends) == 1 and q.get_reservation(r.key).startup_complete
            assert controls.cycle(owner, clock, 102) == 1
            assert len(sends) == 1
    assert q.pool_startup(t.id).startup_attempts == 2


@pytest.mark.parametrize("later", ["started", "exhausted"])
def test_pending_original_receipt_can_complete_after_start_or_exhaust_without_reset(
    q, api, monkeypatch, later,
):
    owner, clock, t, r, probes, nudges, requests, sends = setup(q, api, monkeypatch)
    returns = [("startup_unknown", "lost send")]

    def deliver(invocation, *, observe_only):
        sends.append((invocation, observe_only))
        return returns.pop(0) if returns else ("startup_succeeded", "original receipt recovered")

    monkeypatch.setattr(pool_startup_delivery, "deliver_retry", deliver)
    with owner.client:
        assert controls.cycle(owner, clock, 101) == 0
        if later == "started":
            owner.client.start_pool_task(
                t.id, "host-a/worktree", owner_session_id="native", expected_generation=1,
            )
            before = q.get(t.id), q.pool_startup(t.id).startup_attempts
            assert owner.redrive_unclaimed_spawns(now=102) == 1
        else:
            clock[0] = 701
            owner.client.exhaust_pool_startup(t.id, r.key, generation=1, startup_revision=2)
            before = q.get(t.id), q.pool_startup(t.id).startup_attempts
            assert owner.release_requested_bodies(now=702) == 1
            assert q.get_reservation(r.key).state == SpawnState.RELEASING
        assert (q.get(t.id), q.pool_startup(t.id).startup_attempts) == before
    assert sends[-1][1] is True and q.get_reservation(r.key).startup_complete
    assert len(q.list_reservations(task_id=t.id)) == 1
