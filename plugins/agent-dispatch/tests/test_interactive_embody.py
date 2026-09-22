"""Tests for Phase 1 item 3: the interactive-embodiment transaction
(:mod:`agent_dispatch.interactive_embody`).

Mocks :mod:`agent_dispatch.embody`'s subprocess-shelling functions
(``prepare_reusable_worktree``, ``spawn_embodied_worker``) so the transaction
is exercised end-to-end against a real ``TaskQueue`` with no actual
``agent-worktrees``/Copilot process involved.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from agent_dispatch import embody
from agent_dispatch import interactive_embody
from agent_dispatch.interactive_embody import (
    InteractiveEmbodimentError,
    launch_interactive_embodiment,
)
from agent_dispatch.queue import Status, worker_id_for
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_supervisor import QueueBackedClient as _BaseQueueBackedClient


class QueueBackedClient(_BaseQueueBackedClient):
    """Extends the supervisor tests' client double with the claim/start/
    approve verbs :mod:`agent_dispatch.interactive_embody` needs -- the
    supervisor never calls these itself (it drives spawn reservations, not
    the claim/approve lifecycle), so its own double never defined them."""

    def approve(self, task_id):
        return asdict(self._q.approve(task_id))

    def claim(self, *, task_id, machine=None, worktree=None, repo=None, all_repos=False):
        if not all_repos and not repo:
            raise ValueError("claim requires repo or all_repos=True")
        worker_id = worker_id_for(machine, worktree) if machine and worktree else None
        task = self._q.claim_one(
            worker_id, task_id=task_id, machine=machine, worktree=worktree,
            repo=None if all_repos else repo,
        )
        return asdict(task) if task is not None else None

    def resume(
        self,
        task_id,
        worker_id,
        *,
        wake=True,
        message=None,
        adopt_session=False,
        adopt_owner_session_id=None,
        reuse_session=False,
        expected_owner_session_id=None,
        expected_generation=None,
    ):
        # Mirrors the real coordinator route's mutual-exclusion + direct
        # pass-through of an explicitly-named adopt target (see
        # `ResumeBody`/`client.DispatchClient.resume`).
        if adopt_session and adopt_owner_session_id:
            raise ValueError("resume accepts adopt_session or adopt_owner_session_id, not both")
        return asdict(
            self._q.resume(
                task_id,
                worker_id,
                wake_requested=wake,
                wake_message=message,
                adopt_owner_session_id=adopt_owner_session_id,
                reuse_session=reuse_session,
                expected_owner_session_id=expected_owner_session_id,
                expected_generation=expected_generation,
            )
        )

    def start(self, task_id, worker_id, *, owner_session_id=None):
        return asdict(self._q.start(task_id, worker_id, owner_session_id=owner_session_id))


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture
def client(q):
    return QueueBackedClient(q)


def _mock_prepare(monkeypatch, worktree_id="wt-interactive-1", ownership="created"):
    def fake_prepare(task, reservation, **kwargs):
        return {"worktree": worktree_id, "path": f"/fake/{worktree_id}", "created": True,
                "replaced": False, "ownership": ownership}

    monkeypatch.setattr(interactive_embody.embody, "prepare_reusable_worktree", fake_prepare)


def _mock_spawn_ok(monkeypatch, session_id="cli-session-1", worktree_id="wt-interactive-1"):
    calls = []

    def fake_spawn(task_id, *, worker_id, driver, project, worktree_id: str | None = None,
                   seed=None, timeout=None, **kwargs):
        calls.append(
            {
                "task_id": task_id,
                "project": project,
                "worktree_id": worktree_id,
                "seed": seed,
            }
        )

        class _Proc:
            returncode = 0
            stdout = (
                f'{{"session_id": "{session_id}", "worktree_id": "{worktree_id}"}}'
            )
            stderr = ""

        return _Proc()

    monkeypatch.setattr(interactive_embody.embody, "spawn_embodied_worker", fake_spawn)
    return calls


def _mock_spawn_fail(monkeypatch, *, returncode=1, stderr="boom"):
    def fake_spawn(*_a, **_k):
        class _Proc:
            pass

        proc = _Proc()
        proc.returncode = returncode
        proc.stdout = ""
        proc.stderr = stderr
        return proc

    monkeypatch.setattr(interactive_embody.embody, "spawn_embodied_worker", fake_spawn)


# -- fresh (queued) task ------------------------------------------------------


def test_queued_task_claims_launches_and_starts(q, client, monkeypatch):
    t = q.create("work", repo="github.com/example/repo")
    _mock_prepare(monkeypatch)
    calls = _mock_spawn_ok(monkeypatch)

    result = launch_interactive_embodiment(client, t.id, machine="m")

    assert result["worktree"] == "wt-interactive-1"
    assert result["session"] == "cli-session-1"
    assert result["worker_id"] == "m/wt-interactive-1"
    assert result["project"] == "repo"
    assert calls[0]["project"] == "repo"
    back = q.get(t.id)
    assert back.status == Status.STARTED
    assert back.owner == "m/wt-interactive-1"
    assert back.owner_session_id == "cli-session-1"
    # never injects a worker identity / pool framing into the seed
    assert calls[0]["seed"] is not None
    assert "worker_identities" not in calls[0]["seed"]
    res = q.latest_reservation(t.id)
    assert res.state == "spawned"
    assert res.session_handle == "cli-session-1"


def test_proposed_task_is_approved_first(q, client, monkeypatch):
    t = q.propose("work")
    assert q.get(t.id).status == Status.PROPOSED
    _mock_prepare(monkeypatch)
    _mock_spawn_ok(monkeypatch)

    launch_interactive_embodiment(client, t.id, machine="m")

    assert q.get(t.id).status == Status.STARTED


def test_explicit_project_override_is_returned(q, client, monkeypatch):
    t = q.create("work", repo="github.com/example/repo")
    _mock_prepare(monkeypatch)
    calls = _mock_spawn_ok(monkeypatch)

    result = launch_interactive_embodiment(
        client, t.id, machine="m", project="custom-project"
    )

    assert result["project"] == "custom-project"
    assert calls[0]["project"] == "custom-project"


@pytest.mark.parametrize("status_setter", ["claimed", "started", "completed", "abandoned"])
def test_ineligible_status_is_rejected(q, client, monkeypatch, status_setter):
    t = q.create("work")
    if status_setter in ("claimed", "started", "completed"):
        q.claim_one("m/other", task_id=t.id, machine="m", worktree="other")
        if status_setter in ("started", "completed"):
            q.start(t.id, "m/other")
            if status_setter == "completed":
                q.complete(t.id, "m/other")
    elif status_setter == "abandoned":
        q.abandon(t.id, permitted=True, reason="test")

    with pytest.raises(InteractiveEmbodimentError, match="requires proposed, queued, or suspended"):
        launch_interactive_embodiment(client, t.id, machine="m")
    # no reservation should have been created for a rejected task
    assert q.latest_reservation(t.id) is None


def test_queued_claim_race_releases_reservation(q, client, monkeypatch):
    """Another owner claims the task after this transaction reserved its
    spawn but before its own claim call -- the reservation is released and
    the task is left exactly as the racing claimant left it."""
    t = q.create("work")
    _mock_prepare(monkeypatch)

    real_reserve = q.reserve_spawn

    def racing_reserve(task_id, **kwargs):
        reservation, created = real_reserve(task_id, **kwargs)
        # simulate a concurrent pool worker winning the claim race right
        # after the reservation is taken
        q.claim_one("m/rival", task_id=task_id, machine="m", worktree="rival")
        return reservation, created

    monkeypatch.setattr(q, "reserve_spawn", racing_reserve)

    with pytest.raises(InteractiveEmbodimentError, match="could not be claimed"):
        launch_interactive_embodiment(client, t.id, machine="m")

    assert q.get(t.id).owner == "m/rival"  # the rival's claim is untouched
    assert q.latest_reservation(t.id).state == "failed"


def test_launch_failure_releases_claim_and_reservation(q, client, monkeypatch):
    t = q.create("work")
    _mock_prepare(monkeypatch)
    _mock_spawn_fail(monkeypatch)

    with pytest.raises(InteractiveEmbodimentError, match="boom"):
        launch_interactive_embodiment(client, t.id, machine="m")

    back = q.get(t.id)
    assert back.status == Status.QUEUED  # released, not stuck `claimed`
    assert back.owner is None
    assert q.latest_reservation(t.id).state == "failed"


def test_worktree_preparation_failure_releases_reservation(q, client, monkeypatch):
    t = q.create("work")

    def fake_prepare(*_a, **_k):
        raise embody.EmbodyUnavailable("agent-worktrees CLI not found on PATH")

    monkeypatch.setattr(interactive_embody.embody, "prepare_reusable_worktree", fake_prepare)

    with pytest.raises(InteractiveEmbodimentError, match="agent-worktrees"):
        launch_interactive_embodiment(client, t.id, machine="m")

    assert q.get(t.id).status == Status.QUEUED
    assert q.latest_reservation(t.id).state == "failed"


# -- resuming (suspended) task ------------------------------------------------


def test_suspended_task_resumes_and_reuses_worktree(q, client, monkeypatch):
    t = q.create("work")
    # Simulate the state item 2's auto-suspend leaves behind: a headless-
    # style reservation is NOT what a CLI-embodied suspend carries (no
    # local-body/fleet-body prefix), but the reservation still carries the
    # worktree it was embodied in, per `make_embody_spawn`'s bare handle.
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn_worktree(reservation.key, "wt-existing", ownership="created")
    q.record_spawn(reservation.key, session_handle="stale-dead-session", worktree="wt-existing")
    q.claim_one("m/wt-existing", task_id=t.id, machine="m", worktree="wt-existing")
    q.start(t.id, "m/wt-existing", owner_session_id="stale-dead-session")
    q.suspend(t.id, "m/wt-existing", reason="owner-gone: auto-suspended")
    # PR #2913 review: `reserve_spawn` must not be able to steal an
    # ACTIVE reservation out from under whatever owns it. Auto-suspend
    # itself does not yet settle the reservation it leaves behind (a
    # related, tracked-but-out-of-scope-here gap in Phase 1 item 2's own
    # liveness reconciliation) -- simulate that settlement here so this
    # test exercises interactive-embodiment's OWN contract, not that gap.
    q.settle_spawn(reservation.key, detail="superseded by re-embodiment")
    gen_before = q.get(t.id).generation

    # prepare_reusable_worktree's real reuse branch reports the ALREADY
    # recorded ownership back (`reservation.worktree_ownership or "reused"`)
    # -- match that here so no spurious `record_spawn_worktree` re-write is
    # attempted against the now-`spawned` (no longer `reserving`) reservation.
    _mock_prepare(monkeypatch, worktree_id="wt-existing", ownership="created")
    _mock_spawn_ok(monkeypatch, session_id="fresh-session-2", worktree_id="wt-existing")

    result = launch_interactive_embodiment(client, t.id, machine="m")

    assert result["worktree"] == "wt-existing"
    assert result["session"] == "fresh-session-2"
    back = q.get(t.id)
    assert back.status == Status.STARTED
    assert back.owner == "m/wt-existing"
    assert back.owner_session_id == "fresh-session-2"  # rebound, not the stale one
    assert back.generation != gen_before  # adopt bumps the generation


def test_suspended_task_with_no_prior_reservation_mints_one_from_owner(q, client, monkeypatch):
    """PR #2913 review: a suspended task with NO prior reservation at all
    (e.g. force-stopped, never auto-suspended through a headless reservation)
    must still be re-embodiable -- `reserve_spawn`'s ordinary queued-and-
    unowned gate would otherwise reject it outright (a suspended task always
    retains its owner). `allow_suspended_reembodiment` derives the carried
    worktree straight from the task's own `owner` (`machine/worktree`) rather
    than requiring a pre-existing reservation to carry it."""
    t = q.create("work")
    q.claim_one("m/wt-direct", task_id=t.id, machine="m", worktree="wt-direct")
    q.start(t.id, "m/wt-direct", owner_session_id="s1")
    q.suspend(t.id, "m/wt-direct", reason="force-stopped")
    assert q.latest_reservation(t.id) is None  # nothing to carry forward

    _mock_prepare(monkeypatch, worktree_id="wt-direct", ownership="reused")
    _mock_spawn_ok(monkeypatch, session_id="fresh-session-4", worktree_id="wt-direct")

    result = launch_interactive_embodiment(client, t.id, machine="m")

    assert result["worktree"] == "wt-direct"  # the task's OWN worktree, not a fresh one
    assert result["session"] == "fresh-session-4"
    assert q.get(t.id).status == Status.STARTED


def test_suspended_task_with_active_reservation_refuses_to_steal_it(q, client, monkeypatch):
    """PR #2913 review (Critical): `reserve_spawn` returning `reserved=False`
    means an active reservation already exists and the caller must NOT
    spawn -- `launch_interactive_embodiment` must refuse rather than reuse
    that reservation's key and overwrite its session handle."""
    t = q.create("work")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn_worktree(reservation.key, "wt-existing", ownership="created")
    q.record_spawn(reservation.key, session_handle="still-live-session", worktree="wt-existing")
    q.claim_one("m/wt-existing", task_id=t.id, machine="m", worktree="wt-existing")
    q.start(t.id, "m/wt-existing", owner_session_id="still-live-session")
    q.suspend(t.id, "m/wt-existing", reason="test")
    # Deliberately do NOT settle the reservation -- it's still `spawned`
    # (active), as if the prior session were not actually confirmed gone.

    with pytest.raises(InteractiveEmbodimentError, match="already has an active spawn reservation"):
        launch_interactive_embodiment(client, t.id, machine="m")

    # The untouched original reservation is proof nothing was stolen.
    still = q.latest_reservation(t.id)
    assert still.key == reservation.key
    assert still.session_handle == "still-live-session"


def test_suspended_task_resume_generation_race_releases_reservation(q, client, monkeypatch):
    t = q.create("work")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn_worktree(reservation.key, "wt-existing", ownership="created")
    q.record_spawn(reservation.key, session_handle="stale", worktree="wt-existing")
    q.claim_one("m/wt-existing", task_id=t.id, machine="m", worktree="wt-existing")
    q.start(t.id, "m/wt-existing", owner_session_id="stale")
    q.suspend(t.id, "m/wt-existing", reason="test")
    q.settle_spawn(reservation.key, detail="superseded by re-embodiment")
    stale_generation = q.get(t.id).generation - 1  # deliberately wrong

    _mock_prepare(monkeypatch, worktree_id="wt-existing", ownership="created")
    _mock_spawn_ok(monkeypatch, session_id="fresh-session-3", worktree_id="wt-existing")

    # Force the transaction to capture a stale `expected_generation` by
    # monkeypatching `client.get` to return an outdated generation once.
    real_get = client.get
    calls = {"n": 0}

    def stale_get(task_id):
        calls["n"] += 1
        row = real_get(task_id)
        if calls["n"] == 1:
            row = {**row, "generation": stale_generation}
        return row

    monkeypatch.setattr(client, "get", stale_get)

    with pytest.raises(InteractiveEmbodimentError, match="failed to bind ownership"):
        launch_interactive_embodiment(client, t.id, machine="m")
