"""Tests for the agent-dispatch queue engine."""

from __future__ import annotations

import concurrent.futures
import threading

import pytest

from agent_dispatch.queue import (
    DEFAULT_LEASE_SECONDS,
    LEGACY_REPO,
    Status,
    TaskError,
    machine_matches,
    worker_id_for,
)
from agent_dispatch.queue import TaskQueue as RealTaskQueue
from tests._helpers import OTHER_REPO, TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- basic lifecycle ---------------------------------------------------------


def test_create_defaults_to_queued(q):
    t = q.create("do a thing", prompt="go")
    assert t.status == Status.QUEUED
    assert t.title == "do a thing"
    assert t.prompt == "go"
    assert t.attempts == 0


def test_full_happy_path(q):
    t = q.create("work")
    claimed = q.claim_one("w1")
    assert claimed is not None
    assert claimed.id == t.id
    assert claimed.status == Status.CLAIMED
    assert claimed.owner == "w1"
    assert claimed.attempts == 1
    started = q.start(t.id, "w1")
    assert started.status == Status.STARTED
    done = q.complete(t.id, "w1", result_ref="pr/42")
    assert done.status == Status.COMPLETED
    assert done.result_ref == "pr/42"
    assert done.owner is None


# -- proposed is not claimable ----------------------------------------------


def test_proposed_is_not_claimable(q):
    p = q.propose("draft idea")
    assert p.status == Status.PROPOSED
    assert q.claim_one("w1") is None
    approved = q.approve(p.id)
    assert approved.status == Status.QUEUED
    assert q.claim_one("w1") is not None


# -- atomic claim race -------------------------------------------------------


def test_concurrent_claim_single_winner(q):
    q.create("only one")
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        return q.claim_one(f"w{i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(worker, range(8)))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0].status == Status.CLAIMED


def test_two_queued_two_workers_no_double_claim(q):
    a = q.create("a")
    b = q.create("b")
    r1 = q.claim_one("w1")
    r2 = q.claim_one("w2")
    assert {r1.id, r2.id} == {a.id, b.id}
    assert q.claim_one("w3") is None


# -- liveness GC / recovery --------------------------------------------------


def test_gone_owner_requeues(q):
    t = q.create("leased")
    q.claim_one("m/wt", machine="m", worktree="wt", now=1000.0)
    # owner still live -> never requeued, no matter how much time passes
    assert q.reconcile_liveness(lambda wt, mc, sid: "live", now=9999.0)["requeued"] == 0
    assert q.get(t.id).status == Status.CLAIMED
    # resolver can't tell (bridge down) -> leave it alone (degrade safe)
    assert q.reconcile_liveness(lambda wt, mc, sid: "unknown", now=9999.0)["requeued"] == 0
    assert q.get(t.id).status == Status.CLAIMED
    # owner confirmed gone -> requeued (fenced on owner identity; here owner_session_id
    # is NULL since the task was claimed-not-started, and the fence matches NULL)
    counts = q.reconcile_liveness(lambda wt, mc, sid: "gone", now=2000.0)
    assert counts["checked"] == 1 and counts["gone"] == 1 and counts["requeued"] == 1
    back = q.get(t.id)
    assert back.status == Status.QUEUED
    assert back.owner is None
    # a second worker can now reclaim it
    assert q.claim_one("w2", now=2001.0).owner == "w2"


def test_cooperative_redundancy_after_worker_death(q):
    """A capable second worker reclaims a dead worker's task once it is gone."""
    q.create("review", requires=["review"])
    first = q.claim_one("m/wt", capabilities=["review"], machine="m", worktree="wt", now=1000.0)
    assert first is not None and first.owner == "m/wt"
    # w2 can't claim while the first worker still holds it
    assert q.claim_one("w2", capabilities=["review"], now=1010.0) is None
    q.reconcile_liveness(lambda wt, mc, sid: "gone", now=2000.0)
    second = q.claim_one("w2", capabilities=["review"], now=2001.0)
    assert second is not None and second.owner == "w2"


def test_heartbeat_refreshes_last_seen(q):
    """heartbeat/progress no longer govern recovery (liveness does), but still
    refresh the informational ``lease_expires_at`` (a ``last_seen`` beat)."""
    t = q.create("long")
    q.claim_one("w1", now=1000.0, lease_seconds=60)
    q.heartbeat(t.id, "w1", now=1050.0)
    assert q.get(t.id).lease_expires_at == pytest.approx(1050.0 + DEFAULT_LEASE_SECONDS)


def test_heartbeat_wrong_owner_rejected(q):
    t = q.create("x")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.heartbeat(t.id, "w2")


def test_set_activity_persists_independently_from_task_updated_at(q):
    t = q.create("observed", now=1000.0)
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    observed = q.set_activity(
        t.id, "ACTIVE", reservation_key=reservation.key, now=1010.0
    )
    assert observed.activity == "ACTIVE"
    assert observed.activity_updated_at == 1010.0
    assert observed.updated_at == 1000.0
    cleared = q.set_activity(
        t.id, None, reservation_key=reservation.key, now=1020.0
    )
    assert cleared.activity is None
    assert cleared.activity_updated_at == 1020.0


def test_set_activity_rejects_unknown_value(q):
    t = q.create("observed")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    with pytest.raises(TaskError, match="invalid task activity"):
        q.set_activity(t.id, "IDLE", reservation_key=reservation.key)


def test_state_transition_clears_activity(q):
    t = q.create("observed")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")
    q.set_activity(
        t.id, "ACTIVE", reservation_key=reservation.key, now=1000.0
    )
    done = q.complete(t.id, "w1", now=1010.0)
    assert done.activity is None
    assert done.activity_updated_at == 1010.0


def test_set_activity_rejects_stale_or_wrong_reservation(q):
    t = q.create("observed")
    other = q.create("other")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    wrong, _ = q.reserve_spawn(other.id)
    q.record_spawn(wrong.key, session_handle="local-body:s2")
    with pytest.raises(TaskError, match="active spawned reservation"):
        q.set_activity(t.id, "ACTIVE", reservation_key=wrong.key)
    q.settle_spawn(reservation.key)
    with pytest.raises(TaskError, match="active spawned reservation"):
        q.set_activity(t.id, "ACTIVE", reservation_key=reservation.key)


# -- progress beats ----------------------------------------------------------


def test_record_progress_stores_latest_snapshot(q):
    import json

    t = q.create("work")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.record_progress(
        t.id, "w1", phase="implementing", summary="wired the verb", now=2000.0
    )
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["phase"] == "implementing"
    assert snap["summary"] == "wired the verb"
    assert snap["ts"] == pytest.approx(2000.0)


def test_record_progress_latest_only_overwrites(q):
    import json

    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="planning", summary="first")
    q.record_progress(t.id, "w1", phase="implementing", summary="second")
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["phase"] == "implementing" and snap["summary"] == "second"


def test_record_progress_caps_summary(q):
    import json

    from agent_dispatch.queue import PROGRESS_SUMMARY_MAX

    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="p", summary="x" * 500)
    snap = json.loads(q.get(t.id).latest_progress)
    assert len(snap["summary"]) <= PROGRESS_SUMMARY_MAX
    assert snap["summary"].endswith("\u2026")


def test_record_progress_optional_fields(q):
    import json

    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="pr", summary="opened", pr="pr/42", blocker=None)
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["pr"] == "pr/42"
    assert "blocker" not in snap  # empty/None optional fields are dropped


def test_record_progress_refreshes_last_seen(q):
    t = q.create("work")
    q.claim_one("w1", now=1000.0, lease_seconds=60)
    q.record_progress(t.id, "w1", phase="p", summary="alive", now=1050.0)
    # progress doubles as a last-seen beat: refreshes the informational timestamp
    assert q.get(t.id).lease_expires_at == pytest.approx(1050.0 + DEFAULT_LEASE_SECONDS)


def test_record_progress_wrong_owner_rejected(q):
    t = q.create("work")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.record_progress(t.id, "w2", phase="p", summary="nope")


def test_record_progress_requires_held(q):
    t = q.create("work")  # queued, not held
    with pytest.raises(TaskError):
        q.record_progress(t.id, "w1", phase="p", summary="too early")


def test_record_progress_appends_audit(q):
    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="planning", summary="settled the plan")
    notes = [e.get("note") for e in q.events(t.id)]
    assert any(n and "progress:" in n and "settled the plan" in n for n in notes)


# -- durable goal + append-only progress log ---------------------------------


def test_goal_and_done_criteria_round_trip(q):
    t = q.create(
        "improve one thing",
        prompt="pick something and improve it",
        goal="raise test coverage of module X",
        done_criteria="coverage >= 90% and CI green",
    )
    assert t.goal == "raise test coverage of module X"
    assert t.done_criteria == "coverage >= 90% and CI green"
    # Re-read from the store: fields persist on the row.
    got = q.get(t.id)
    assert got.goal == "raise test coverage of module X"
    assert got.done_criteria == "coverage >= 90% and CI green"


def test_create_without_goal_defaults_to_none(q):
    t = q.create("plain one-shot")
    assert t.goal is None
    assert t.done_criteria is None
    assert q.progress_log(t.id) == []


def test_record_progress_appends_to_progress_log(q):
    import json

    t = q.create("work", goal="reach the goal", done_criteria="it is done")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.record_progress(t.id, "w1", phase="planning", summary="first pass", now=1000.0)
    q.record_progress(t.id, "w1", phase="implementing", summary="second pass", now=2000.0)

    # latest-only beat still overwrites (no regression).
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["phase"] == "implementing" and snap["summary"] == "second pass"

    # append-only log accumulates BOTH beats, in chronological order.
    log = q.progress_log(t.id)
    assert [(r["phase"], r["summary"], r["worker"]) for r in log] == [
        ("planning", "first pass", "w1"),
        ("implementing", "second pass", "w1"),
    ]
    assert log[0]["ts"] == pytest.approx(1000.0)
    assert log[1]["ts"] == pytest.approx(2000.0)


def test_progress_log_carries_detail_and_blocker(q):
    t = q.create("work")
    q.claim_one("w1")
    # An explicit detail wins.
    q.record_progress(t.id, "w1", phase="p", summary="s", detail="a longer note")
    # Otherwise the beat's blocker/pr context becomes the log detail.
    q.record_progress(t.id, "w1", phase="pr", summary="opened", pr="pr/42")
    log = q.progress_log(t.id)
    assert log[0]["detail"] == "a longer note"
    assert log[1]["detail"] == "pr: pr/42"


def test_progress_log_empty_for_untouched_task(q):
    t = q.create("work")
    assert q.progress_log(t.id) == []


# -- capability gating -------------------------------------------------------


def test_requires_gates_claim(q):
    q.create("logging", requires=["logger"])
    assert q.claim_one("plain") is None
    assert q.claim_one("plain", capabilities=["logger"]) is not None


def test_identity_pin_via_requires(q):
    q.create("review", requires=["agent:review-bot"])
    assert q.claim_one("random", capabilities=["review"]) is None
    got = q.claim_one("review-bot", capabilities=["agent:review-bot"])
    assert got is not None


def test_affinity_orders_but_does_not_exclude(q):
    generic = q.create("generic")
    preferred = q.create("preferred", affinity={"agent": "w1"})
    # w1 prefers the affinity task even though the generic one is older
    got = q.claim_one("w1")
    assert got.id == preferred.id
    # a different worker still gets the remaining task (affinity never excludes)
    other = q.claim_one("w2")
    assert other.id == generic.id


# -- not_before scheduling ---------------------------------------------------


def test_not_before_defers_claim(q):
    q.create("later", not_before=5000.0)
    assert q.claim_one("w1", now=4000.0) is None
    assert q.claim_one("w1", now=5001.0) is not None


# -- dedup -------------------------------------------------------------------


def test_dedup_key_prevents_duplicate(q):
    a = q.create("dup", dedup_key="k1")
    b = q.create("dup again", dedup_key="k1")
    assert a.id == b.id
    assert len(q.list()) == 1


# -- yield / abandon ---------------------------------------------------------


def test_yield_returns_to_queued_with_updates(q):
    t = q.create("conflict")
    q.claim_one("w1")
    q.start(t.id, "w1")
    y = q.yield_task(t.id, "w1", note="merge conflict")
    assert y.status == Status.QUEUED
    assert y.owner is None
    assert q.claim_one("w2") is not None


def test_abandon_requires_permission(q):
    t = q.create("bad")
    with pytest.raises(TaskError):
        q.abandon(t.id)
    done = q.abandon(t.id, permitted=True, reason="duplicate")
    assert done.status == Status.ABANDONED


def test_terminal_states_reject_transitions(q):
    t = q.create("x")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.complete(t.id, "w1")
    with pytest.raises(TaskError):
        q.start(t.id, "w1")


def test_start_wrong_owner_rejected(q):
    t = q.create("x")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.start(t.id, "w2")


# -- detach (worktree portability) ------------------------------------------


def test_detach_demotes_hard_worktree_pin(q):
    t = q.create("handoff", requires=["worktree:wt-1"], target_worktree="wt-1")
    d = q.detach(t.id)
    assert "worktree:wt-1" not in d.requires
    assert d.affinity.get("worktree") == "wt-1"
    # now claimable by any worker (pin demoted to a soft preference)
    assert q.claim_one("anyone") is not None


# -- migration idempotency ---------------------------------------------------


def test_reopen_existing_db_is_idempotent(tmp_path):
    db = tmp_path / "tasks.db"
    q1 = TaskQueue(db)
    t = q1.create("persist")
    q2 = TaskQueue(db)  # re-run migrations on an existing DB
    assert q2.get(t.id).title == "persist"


# -- audit trail -------------------------------------------------------------


def test_events_record_transitions(q):
    t = q.create("audited")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.complete(t.id, "w1")
    trail = [e["to_status"] for e in q.events(t.id)]
    assert trail == [Status.QUEUED, Status.CLAIMED, Status.STARTED, Status.COMPLETED]


# -- worker identity + targeting-in-claim ------------------------------------


def test_worker_id_for():
    assert worker_id_for("host-a", "wt-1") == "host-a/wt-1"


def test_claim_gated_by_target_machine(q):
    q.create("m1-only", target_machine="m1")
    assert q.claim_one("a", machine="m2", worktree="w") is None
    got = q.claim_one("a", machine="m1", worktree="w")
    assert got is not None and got.target_machine == "m1"


def test_machine_matches_case_insensitive():
    # Unset target -> machine-agnostic, matches anyone (including no machine).
    assert machine_matches(None, "anomalous-potato") is True
    assert machine_matches(None, None) is True
    # Same name in different case still matches (display_name vs identity).
    assert machine_matches("anomalous-potato", "Anomalous-Potato") is True
    assert machine_matches("Anomalous-Potato", "anomalous-potato") is True
    # Genuinely different machines don't match; a set target needs a machine.
    assert machine_matches("emancipation-cube", "anomalous-potato") is False
    assert machine_matches("anomalous-potato", None) is False


def test_claim_gated_by_target_machine_case_insensitive(q):
    # A task stored with a display-cased target_machine is still claimable by the
    # agent whose (canonical, lowercase) identity names the same machine.
    q.create("cased", target_machine="Anomalous-Potato")
    got = q.claim_one("a", machine="anomalous-potato", worktree="w")
    assert got is not None and got.target_machine == "Anomalous-Potato"


def test_list_target_machine_filter_case_insensitive(q):
    q.create("t", target_machine="anomalous-potato")
    assert len(q.list(target_machine="Anomalous-Potato")) == 1
    assert len(q.list(target_machine="anomalous-potato")) == 1
    assert len(q.list(target_machine="emancipation-cube")) == 0


def test_claim_gated_by_target_worktree(q):
    q.create("wtX-only", target_worktree="wtX")
    assert q.claim_one("a", machine="m", worktree="other") is None
    assert q.claim_one("a", machine="m", worktree="wtX") is not None


def test_untargeted_task_claimable_by_any_identity(q):
    q.create("open")
    assert q.claim_one("a", machine="m", worktree="w") is not None


def test_machineless_claimer_gets_only_untargeted(q):
    q.create("targeted", target_machine="m1")
    q.create("open")
    got = q.claim_one("a")  # no machine/worktree declared
    assert got.title == "open"


def test_claim_stamps_composite_owner(q):
    t = q.create("x")
    owner = worker_id_for("host-a", "wt-9")
    got = q.claim_one(owner, machine="host-a", worktree="wt-9", task_id=t.id)
    assert got.owner == "host-a/wt-9"


def test_mine_returns_assigned_and_owned(q):
    assigned = q.create("for-wt1", target_worktree="wt-1")
    machine_wide = q.create("for-machine", target_machine="host-a")
    to_own = q.create("to-own")
    q.claim_one(
        worker_id_for("host-a", "wt-1"),
        machine="host-a",
        worktree="wt-1",
        task_id=to_own.id,
    )
    q.create("open-to-all")  # untargeted -- not "assigned to me"

    inbox = q.mine("host-a", "wt-1")
    assigned_ids = {t.id for t in inbox["assigned"]}
    owned_ids = {t.id for t in inbox["owned"]}
    assert assigned.id in assigned_ids
    assert machine_wide.id in assigned_ids  # machine-wide, no worktree pin
    assert to_own.id in owned_ids
    assert all(t.title != "open-to-all" for t in inbox["assigned"])


def test_mine_matches_machine_wide_assignment_case_insensitively(q):
    # A machine-wide assignment stored display-cased is still "mine" when my
    # identity names the same machine in canonical (lowercase) form.
    machine_wide = q.create("for-machine", target_machine="Anomalous-Potato")
    inbox = q.mine("anomalous-potato", "wt-1")
    assert machine_wide.id in {t.id for t in inbox["assigned"]}


# -- browse: multi-status list + dedup sweep ---------------------------------


def _seed_all_states(q):
    """Create one task in each of the six states; return {state: task}."""
    proposed = q.propose("proposed one", prompt="p")
    queued = q.create("queued one", prompt="q")

    claimed_t = q.create("claimed one", prompt="c")
    q.claim_one("w", task_id=claimed_t.id)

    started_t = q.create("started one", prompt="s")
    q.claim_one("w", task_id=started_t.id)
    q.start(started_t.id, "w")

    completed_t = q.create("completed one", prompt="done")
    q.claim_one("w", task_id=completed_t.id)
    q.start(completed_t.id, "w")
    q.complete(completed_t.id, "w")

    abandoned_t = q.create("abandoned one", prompt="x")
    q.abandon(abandoned_t.id, permitted=True)

    return {
        Status.PROPOSED: proposed,
        Status.QUEUED: queued,
        Status.CLAIMED: claimed_t,
        Status.STARTED: started_t,
        Status.COMPLETED: completed_t,
        Status.ABANDONED: abandoned_t,
    }


def test_list_single_status_still_works(q):
    seed = _seed_all_states(q)
    got = q.list(status=Status.QUEUED)
    assert [t.id for t in got] == [seed[Status.QUEUED].id]


def test_list_accepts_multiple_statuses(q):
    seed = _seed_all_states(q)
    got = q.list(status=[Status.QUEUED, Status.STARTED])
    assert {t.id for t in got} == {seed[Status.QUEUED].id, seed[Status.STARTED].id}


def test_list_empty_status_sequence_matches_all(q):
    _seed_all_states(q)
    # An empty sequence adds no clause -> behaves like an unfiltered list.
    assert len(q.list(status=[])) == 6


def test_sweep_spans_all_states_except_abandoned(q):
    seed = _seed_all_states(q)
    swept = {t.id for t in q.sweep()}
    assert swept == {
        seed[s].id
        for s in (
            Status.PROPOSED,
            Status.QUEUED,
            Status.CLAIMED,
            Status.STARTED,
            Status.COMPLETED,
        )
    }
    assert seed[Status.ABANDONED].id not in swept


def test_sweep_is_newest_first(q):
    q.create("first")
    q.create("second")
    titles = [t.title for t in q.sweep()]
    assert titles[:2] == ["second", "first"]


# -- repo lane (scoping / isolation) -----------------------------------------


def test_create_requires_repo(tmp_path):
    q = RealTaskQueue(tmp_path / "t.db")  # no defaulting -- repo is mandatory
    with pytest.raises(TaskError):
        q.create("no lane")


def test_list_find_sweep_are_lane_scoped(q):
    a = q.create("alpha task", repo=TEST_REPO)
    b = q.create("beta task", repo=OTHER_REPO)
    assert [t.id for t in q.list(repo=TEST_REPO)] == [a.id]
    assert [t.id for t in q.sweep(repo=OTHER_REPO)] == [b.id]
    assert {t.id for t in q.find("task", repo=TEST_REPO)} == {a.id}
    # unscoped list still sees both (engine default; the CLI always scopes)
    assert {t.id for t in q.list()} == {a.id, b.id}


def test_claim_never_crosses_lanes(q):
    a = q.create("in my lane", repo=TEST_REPO)
    b = q.create("other lane", repo=OTHER_REPO)
    got = q.claim_one("w", repo=OTHER_REPO)
    assert got is not None and got.id == b.id  # never the TEST_REPO task
    assert q.get(a.id).status == Status.QUEUED  # untouched


def test_claim_by_id_respects_lane(q):
    a = q.create("mine", repo=TEST_REPO)
    # a worker in another lane can't claim it even by explicit id
    assert q.claim_one("w", repo=OTHER_REPO, task_id=a.id) is None
    # same lane succeeds
    got = q.claim_one("w", repo=TEST_REPO, task_id=a.id)
    assert got is not None and got.id == a.id


def test_mine_is_lane_scoped(q):
    here = q.create("for wt in my lane", repo=TEST_REPO, target_worktree="wt-1")
    other = q.create("for wt other lane", repo=OTHER_REPO, target_worktree="wt-1")
    inbox = q.mine("m", "wt-1", repo=TEST_REPO)
    ids = {t.id for t in inbox["assigned"]}
    assert here.id in ids and other.id not in ids


def test_sentinel_backfill_on_migration(tmp_path):
    import sqlite3

    db = tmp_path / "legacy.db"
    q = RealTaskQueue(db)
    t = q.create("legacy row", repo="temp")
    # Simulate a pre-repo row by nulling the lane, then reopen to re-migrate.
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET repo = NULL WHERE id = ?", (t.id,))
    con.commit()
    con.close()
    q2 = RealTaskQueue(db)
    assert q2.get(t.id).repo == LEGACY_REPO
