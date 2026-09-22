"""Tests for liveness garbage collection -- the lease -> GC recovery model.

Covers the identity-keyed tri-state resolver (`tracking.liveness_verdict`), the
fenced `TaskQueue.reconcile_liveness` GC pass (owner-session identity, not
worktree occupancy), the dead-letter cap, and the `backlog_health` buildup
signal broken out by liveness.
"""

from __future__ import annotations

import pytest

from agent_dispatch import tracking
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


class _Proc:
    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


def _fake_run(returncode=0, stdout="", raises=None):
    def run(*_a, **_k):
        if raises is not None:
            raise raises
        return _Proc(returncode, stdout)
    return run


def _bridge_ok(monkeypatch):
    monkeypatch.setattr(
        tracking, "agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )


def _claim_and_start(q, task_id, *, wt, session, now):
    """Claim + start a task so it carries a captured owner_session_id.

    No headless spawn reservation is attached, so :meth:`TaskQueue
    .reconcile_liveness` treats this as the CLI-embodied/generic path (see
    ``test_reconcile_gone_cli_embodied_started_task_is_suspended`` below) --
    use :func:`_claim_and_start_headless` instead for the pre-existing
    headless-body requeue/dead-letter tests.
    """
    q.claim_one(f"m/{wt}", machine="m", worktree=wt, task_id=task_id, now=now)
    q.start(task_id, f"m/{wt}", owner_session_id=session, now=now + 1)


def _claim_and_start_headless(q, task_id, *, wt, session, now):
    """Claim + start a task behind a local headless spawn reservation.

    Mirrors :func:`_claim_and_start`, but first reserves+records a
    ``local-body:<session>`` spawn reservation so
    :meth:`TaskQueue.reconcile_liveness` classifies it as a **headless** body
    (probed via ``headless_local_verdict``, never the worktree-keyed CLI
    resolver) -- the "headless dead-session behavior stays UNCHANGED" case.
    """
    reservation, _ = q.reserve_spawn(task_id, now=now)
    q.record_spawn(reservation.key, session_handle=f"local-body:{session}", now=now)
    q.claim_one(f"m/{wt}", machine="m", worktree=wt, task_id=task_id, now=now)
    q.start(task_id, f"m/{wt}", owner_session_id=session, now=now + 1)


# -- tri-state, identity-keyed liveness_verdict ------------------------------


def test_verdict_live_when_current_session_matches_owner(monkeypatch):
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(
        tracking, "run_background_capture", _fake_run(0, '{"session_id": "S1"}')
    )
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.LIVE


def test_verdict_gone_when_worktree_reused_by_different_session(monkeypatch):
    # A DIFFERENT session now occupies the worktree -> our owner is gone (the
    # reused-worktree false-negative the design closes).
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(
        tracking, "run_background_capture", _fake_run(0, '{"session_id": "S2"}')
    )
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.GONE


def test_verdict_gone_when_worktree_empty(monkeypatch):
    # `{}` (CLI 404): no session occupies the worktree at all -> owner gone.
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking, "run_background_capture", _fake_run(0, "{}"))
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.GONE


def test_verdict_unknown_when_owner_identity_not_captured(monkeypatch):
    # Even with a live session present, no captured owner identity means we can't
    # attribute it -> unknown (the claim-before-registration false-positive guard).
    # This holds even when the bridge answers empty and the local
    # agent-worktrees registry would confirm the worktree gone: this shared
    # resolver serves every claimed/started task's liveness GC, not only a
    # spawn reservation's own worktree, so it never escalates an uncaptured
    # owner_session_id to GONE by itself (see `worktree_directory_present` for
    # the narrowly-scoped exception a spawn-reservation-owning caller may
    # layer on top).
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(
        tracking, "run_background_capture", _fake_run(0, '{"session_id": "S1"}')
    )
    assert tracking.liveness_verdict("wt", owner_session_id=None) == tracking.UNKNOWN
    monkeypatch.setattr(tracking, "run_background_capture", _fake_run(0, "{}"))
    assert tracking.liveness_verdict("wt", owner_session_id=None) == tracking.UNKNOWN




@pytest.mark.parametrize(
    "run",
    [
        _fake_run(1, "{}"),                 # non-zero exit -> bridge errored
        _fake_run(0, ""),                    # exit 0 but silent -> ambiguous
        _fake_run(0, "not json"),            # unparseable
        _fake_run(0, "[]"),                  # valid JSON but not an object
        lambda *_a, **_k: None,               # spawn error/timeout translated
    ],
)
def test_verdict_unknown_on_any_resolver_failure(monkeypatch, run):
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking, "run_background_capture", run)
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.UNKNOWN


def test_verdict_unknown_when_bridge_absent(monkeypatch):
    monkeypatch.setattr(tracking, "agent_bridge_launch_prefix", lambda: None)
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.UNKNOWN


def test_verdict_unknown_on_empty_worktree_handle():
    assert tracking.liveness_verdict("", owner_session_id="S1") == tracking.UNKNOWN
    assert tracking.liveness_verdict(None, owner_session_id="S1") == tracking.UNKNOWN


def test_verdict_uses_local_bridge_when_machine_is_this_machine(monkeypatch):
    # Regression: every claimed/started task's stored owner is
    # "<machine>/<worktree>", so `machine` is non-empty for *every* task, local
    # or remote. Without gating it through `is_peer_machine`, a local task's
    # liveness probe would shell an unnecessary self-SSH loopback (a visible
    # OpenSSH window flash on every GC pass) instead of the direct local
    # `agent-bridge` call every other caller in this module already takes.
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking.remote_dispatch, "is_peer_machine", lambda _m: False)

    def local_only(*_a, **_k):
        return _Proc(0, '{"session_id": "S1"}')

    def ssh_forbidden(*_a, **_k):
        raise AssertionError("must not shell SSH for this machine's own owner")

    monkeypatch.setattr(tracking, "run_background_capture", local_only)
    monkeypatch.setattr(tracking, "run_ssh_capture", ssh_forbidden)
    assert (
        tracking.liveness_verdict(
            "wt", machine="cloud1", owner_session_id="S1"
        )
        == tracking.LIVE
    )


def test_verdict_still_uses_ssh_for_a_genuine_peer_machine(monkeypatch):
    monkeypatch.setattr(tracking.remote_dispatch, "is_peer_machine", lambda _m: True)

    def local_forbidden(*_a, **_k):
        raise AssertionError("must not use the local bridge for a genuine peer")

    def ssh_only(*_a, **_k):
        return _Proc(0, '{"session_id": "S1"}')

    monkeypatch.setattr(tracking, "run_background_capture", local_forbidden)
    monkeypatch.setattr(tracking, "run_ssh_capture", ssh_only)
    assert (
        tracking.liveness_verdict("wt", machine="peer-box", owner_session_id="S1")
        == tracking.LIVE
    )


def test_verdict_satellite_uses_pushed_status_live(monkeypatch):
    # A registered role=satellite owner opens no inbound listener -- resolve
    # from its pushed directory status instead of ever touching SSH (see the
    # `satellite-agent-exposure` effort's Design item D and the matching
    # `resolve_live_session` branch this mirrors).
    monkeypatch.setattr(tracking.remote_dispatch, "is_peer_machine", lambda _m: True)
    monkeypatch.setattr(
        tracking,
        "_satellite_roster",
        lambda: {"book2": {"status": {"wt": {"session_id": "S1"}}}},
    )

    def ssh_forbidden(*_a, **_k):
        raise AssertionError("must not shell ssh for a registered satellite")

    monkeypatch.setattr(tracking, "run_ssh_capture", ssh_forbidden)
    monkeypatch.setattr(tracking, "run_background_capture", ssh_forbidden)
    assert (
        tracking.liveness_verdict("wt", machine="book2", owner_session_id="S1")
        == tracking.LIVE
    )


def test_verdict_satellite_gone_when_different_session_or_worktree_absent(
    monkeypatch,
):
    monkeypatch.setattr(tracking.remote_dispatch, "is_peer_machine", lambda _m: True)
    monkeypatch.setattr(
        tracking,
        "_satellite_roster",
        lambda: {"book2": {"status": {"wt": {"session_id": "S2"}}}},
    )
    assert (
        tracking.liveness_verdict("wt", machine="book2", owner_session_id="S1")
        == tracking.GONE
    )

    # The satellite is registered but this worktree isn't in its pushed
    # status at all -- exactly like the SSH-back `{}` empty-registry answer.
    monkeypatch.setattr(
        tracking, "_satellite_roster", lambda: {"book2": {"status": {}}}
    )
    assert (
        tracking.liveness_verdict("wt", machine="book2", owner_session_id="S1")
        == tracking.GONE
    )


def test_verdict_satellite_unknown_when_owner_identity_not_captured(monkeypatch):
    monkeypatch.setattr(tracking.remote_dispatch, "is_peer_machine", lambda _m: True)
    monkeypatch.setattr(
        tracking,
        "_satellite_roster",
        lambda: {"book2": {"status": {"wt": {"session_id": "S1"}}}},
    )
    assert (
        tracking.liveness_verdict("wt", machine="book2", owner_session_id=None)
        == tracking.UNKNOWN
    )


# -- reconcile_liveness: fenced, identity-keyed GC ---------------------------


def test_reconcile_gone_cli_embodied_task_is_suspended_not_requeued(q):
    """A gone owner with **no** headless spawn reservation is treated as the
    CLI-embodied/generic path: it is auto-suspended (Phase 1 item 2), never
    silently requeued/dead-lettered out from under a durable worktree."""
    a = q.create("a", now=1000.0)
    b = q.create("b", now=1001.0)
    _claim_and_start(q, a.id, wt="wtA", session="SA", now=1002.0)
    _claim_and_start(q, b.id, wt="wtB", session="SB", now=1003.0)
    # A stale headless-style beat left on the row (PR #2913 review: without
    # the fix, auto-suspend leaves this untouched, so a confirmed-gone task
    # can keep showing LIVE via the Tasks pane's `wt_live` column).
    with q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET activity = 'ACTIVE', activity_updated_at = ? WHERE id = ?",
            (1002.0, a.id),
        )

    def resolver(wt, mc, sid):
        return {"wtA": "gone", "wtB": "live"}[wt]

    counts = q.reconcile_liveness(resolver, now=5000.0)
    assert counts["checked"] == 2
    assert counts["gone"] == 1 and counts["live"] == 1
    assert counts["suspended"] == 1
    assert counts["requeued"] == 0 and counts["dead_lettered"] == 0
    gone_task = q.get(a.id)
    assert gone_task.status == Status.SUSPENDED
    # Owner/owner-session identity is retained (unlike the headless requeue
    # path) so a fresh interactive-embodiment session can resume + rebind it.
    assert gone_task.owner == "m/wtA" and gone_task.owner_session_id == "SA"
    # Activity fields are cleared -- matching manual TaskQueue.suspend()'s own
    # behavior (via `_transition`'s "leaving the held lifecycle" rule).
    assert gone_task.activity is None
    assert gone_task.activity_updated_at is None
    assert q.get(b.id).status == Status.STARTED  # a live owner is never disturbed


def test_reconcile_gone_headless_task_still_requeues(q):
    """Headless dead-session behavior is UNCHANGED by the new CLI-embodied
    auto-suspend branch: a headless body confirmed gone still requeues exactly
    as before (fenced, ownership cleared)."""
    t = q.create("headless", now=1000.0)
    _claim_and_start_headless(q, t.id, wt="wt", session="S1", now=1002.0)

    counts = q.reconcile_liveness(headless_local_verdict=lambda sid: "gone", now=5000.0)
    assert counts["checked"] == 1 and counts["gone"] == 1
    assert counts["requeued"] == 1 and counts["suspended"] == 0
    back = q.get(t.id)
    assert back.status == Status.QUEUED
    assert back.owner is None and back.owner_session_id is None


def test_reconcile_unknown_never_requeues(q):
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    counts = q.reconcile_liveness(lambda wt, mc, sid: "unknown", now=1_000_000.0)
    assert counts["requeued"] == 0
    assert q.get(t.id).status == Status.STARTED


def test_reconcile_persists_last_liveness_for_metric(q):
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    q.reconcile_liveness(lambda wt, mc, sid: "live", now=1010.0)
    assert q.get(t.id).last_liveness == "live"


def test_reconcile_fence_no_ops_when_owner_reclaimed_midpass(q):
    """If the task is re-claimed (new generation) between the probe and the write,
    the fenced requeue must no-op -- no clobber of the new owner. Uses a
    headless reservation so the outcome under test is the (still-requeuing)
    headless path -- a suspended task cannot be plainly re-claimed like a
    queued one, so this fencing scenario doesn't translate to the CLI path."""
    t = q.create("x", now=1000.0)
    _claim_and_start_headless(q, t.id, wt="wt", session="S1", now=1001.0)
    gen_before = q.get(t.id).generation

    # A verdict fn that, after being asked, requeues + reclaims the task under
    # a new generation -- simulating the probe->write race window.
    def racing_verdict(sid):
        q.reconcile_liveness(headless_local_verdict=lambda _sid: "gone", now=1002.0)  # requeue under S1...
        q.claim_one("m/wt2", machine="m", worktree="wt2", task_id=t.id, now=1003.0)  # ...reclaimed
        return "gone"

    counts = q.reconcile_liveness(headless_local_verdict=racing_verdict, now=1004.0)
    assert counts["requeued"] == 0  # outer write fenced out by the generation change
    assert q.get(t.id).generation != gen_before


def test_reconcile_dead_letters_past_attempt_cap(q):
    t = q.create("poison", now=1000.0)
    reservation, _ = q.reserve_spawn(t.id, now=1000.0)
    q.record_spawn(reservation.key, session_handle="local-body:S1", now=1000.0)
    for i in range(10):
        st = q.get(t.id)
        if st.status == Status.DEAD_LETTER:
            break
        if st.status in (Status.QUEUED, Status.PROPOSED):
            q.claim_one("m/wt", machine="m", worktree="wt", task_id=t.id, now=1100.0 + i)
            q.start(t.id, "m/wt", owner_session_id="S1", now=1100.0 + i + 0.5)
        q.reconcile_liveness(
            headless_local_verdict=lambda sid: "gone", max_attempts=3, now=1200.0 + i
        )
    assert q.get(t.id).status == Status.DEAD_LETTER


def test_reconcile_ignores_unheld_tasks(q):
    q.create("queued-only", now=1000.0)  # never claimed
    counts = q.reconcile_liveness(lambda wt, mc, sid: "gone", now=2000.0)
    assert counts["checked"] == 0 and counts["requeued"] == 0


def test_reconcile_probes_held_tasks_concurrently_not_serially(q):
    """Each held task's liveness probe is a blocking call with its own
    multi-second real-world timeout (``tracking.liveness_verdict``: 3-6s SSH/
    bridge subprocess). A large held backlog probed one-at-a-time could
    serially exceed a GC loop's overall cycle timeout -- and since verdicts
    were only written after *every* probe finished, a mid-loop timeout meant
    none of that cycle's transitions landed, letting stale/`unknown` tasks
    and cold reservations pile up unreaped indefinitely (the root cause
    behind a real production `liveness_gc: cycle exceeded 60.0s` incident).

    Probing must therefore run concurrently: wall-clock time for N held tasks
    each with a slow resolver should track the slowest single probe, not the
    sum of all of them.
    """
    import time

    n = 6
    delay = 0.2
    for i in range(n):
        t = q.create(f"t{i}", now=1000.0 + i)
        _claim_and_start(q, t.id, wt=f"wt{i}", session=f"S{i}", now=1001.0 + i)

    def slow_resolver(wt, mc, sid):
        time.sleep(delay)
        return "live"

    started = time.monotonic()
    counts = q.reconcile_liveness(slow_resolver, now=9999.0)
    elapsed = time.monotonic() - started

    assert counts["checked"] == n and counts["live"] == n
    # Serial would take >= n * delay (1.2s here); concurrent should stay well
    # under half that -- generous enough to avoid CI flakiness while still
    # failing hard against a serial implementation.
    assert elapsed < (n * delay) / 2


def test_reconcile_default_resolver_safe_without_bridge(q, monkeypatch):
    monkeypatch.setattr(tracking, "agent_bridge_launch_prefix", lambda: None)
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    assert q.reconcile_liveness(now=9999.0)["requeued"] == 0
    assert q.get(t.id).status == Status.STARTED


def test_recover_expired_leases_shim_delegates(q, monkeypatch):
    from agent_dispatch import spawn_factories

    # Headless: the shim's "requeued" contract (`recover_expired_leases`'s
    # docstring) is exercised on the path whose behavior is unchanged by the
    # new CLI-embodied auto-suspend branch.
    monkeypatch.setattr(spawn_factories, "_default_local_body_verdict", lambda *a, **k: "gone")
    t = q.create("x", now=1000.0)
    _claim_and_start_headless(q, t.id, wt="wt", session="S1", now=1001.0)
    assert q.recover_expired_leases() == 1  # back-compat int return
    assert q.get(t.id).status == Status.QUEUED


# -- backlog_health (buildup, broken out by liveness) ------------------------


def test_backlog_health_empty(q):
    h = q.backlog_health(now=1000.0)
    assert h["queued"] == 0 and h["held"] == 0 and h["dead_letter"] == 0
    assert h["oldest_queued_age"] is None and h["oldest_held_live_age"] is None


def test_backlog_health_breaks_out_held_by_liveness(q):
    q.create("old", now=1000.0)  # queued
    live = q.create("live-held", now=1002.0)
    _claim_and_start(q, live.id, wt="wtL", session="SL", now=1003.0)  # last_seen at 1004
    q.reconcile_liveness(lambda wt, mc, sid: "live", now=1010.0)

    h = q.backlog_health(now=1100.0)
    assert h["queued"] == 1
    assert h["held"] == 1 and h["held_live"] == 1
    assert h["oldest_queued_age"] == pytest.approx(100.0)  # 1100 - 1000
    # stuck-but-alive signal: age since the live-held task last progressed (1004)
    assert h["oldest_held_live_age"] == pytest.approx(96.0)


def test_backlog_health_counts_suspended_outside_held(q):
    task = q.create("dormant", now=1000.0)
    _claim_and_start(
        q, task.id, wt="wtS", session="SS", now=1001.0
    )
    q.suspend(
        task.id, "m/wtS", reason="waiting for an external result", now=1003.0
    )

    health = q.backlog_health(now=1100.0)
    assert health["suspended"] == 1
    assert health["held"] == 0
    assert health["held_live"] == 0


# -- orphaned-pin reaper: unowned tasks whose target worktree is gone ---------


class TestReapOrphanedTargets:
    def _pinned(self, q, title, *, wt, machine="m", now, status=Status.PROPOSED):
        return q.create(
            title, status=status, target_machine=machine, target_worktree=wt,
            source="context-handoff", labels=["handoff"], now=now,
        )

    def test_reaps_orphaned_proposed(self, q):
        t = self._pinned(q, "h1", wt="wt-gone", now=100)
        counts = q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=0, now=200)
        assert counts["reaped"] == 1 and counts["orphaned"] == 1
        assert q.get(t.id).status == Status.ABANDONED

    def test_reaps_orphaned_queued(self, q):
        t = self._pinned(q, "h2", wt="wt-gone", now=100, status=Status.QUEUED)
        counts = q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=0, now=200)
        assert counts["reaped"] == 1
        assert q.get(t.id).status == Status.ABANDONED

    def test_keeps_task_for_live_worktree(self, q):
        t = self._pinned(q, "h3", wt="wt-live", now=100)
        counts = q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=0, now=200)
        assert counts["reaped"] == 0
        assert q.get(t.id).status == Status.PROPOSED

    def test_respects_grace_window(self, q):
        # 10s old at now=200; a 1h grace keeps it even though the worktree is gone.
        t = self._pinned(q, "h4", wt="wt-gone", now=190)
        counts = q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=3600, now=200)
        assert counts["reaped"] == 0
        assert q.get(t.id).status == Status.PROPOSED

    def test_degrade_safe_when_probe_none(self, q):
        # An unresolved live-worktree probe reaps nothing (never act on ignorance).
        t = self._pinned(q, "h5", wt="wt-gone", now=100)
        counts = q.reap_orphaned_targets(None, machine="m", grace_secs=0, now=200)
        assert counts == {"checked": 0, "orphaned": 0, "reaped": 0}
        assert q.get(t.id).status == Status.PROPOSED

    def test_only_local_machine_tasks(self, q):
        # A task targeting another machine is that coordinator's to reap.
        t = self._pinned(q, "h6", wt="wt-gone", machine="other", now=100)
        counts = q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=0, now=200)
        assert counts["checked"] == 0
        assert q.get(t.id).status == Status.PROPOSED

    def test_machine_match_is_case_insensitive(self, q):
        self._pinned(q, "h7", wt="wt-gone", machine="cloud1", now=100)
        counts = q.reap_orphaned_targets(
            {"wt-live"}, machine="cloud1", grace_secs=0, now=200)
        assert counts["reaped"] == 1

    def test_ignores_owned_held_tasks(self, q):
        # An owned (claimed/started) task is reconcile_liveness's job, not this.
        t = q.create("owned", status=Status.QUEUED, target_machine="m",
                     target_worktree="wt-gone", now=100)
        _claim_and_start(q, t.id, wt="wt-gone", session="S1", now=100)
        counts = q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=0, now=200)
        assert counts["reaped"] == 0
        assert q.get(t.id).status == Status.STARTED

    def test_ignores_untargeted_tasks(self, q):
        # No target_worktree -> not a pinned task -> never reaped.
        t = q.create("floating", status=Status.QUEUED, target_machine="m", now=100)
        counts = q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=0, now=200)
        assert counts["checked"] == 0
        assert q.get(t.id).status == Status.QUEUED

    def test_abandon_stamps_completed_at(self, q):
        t = self._pinned(q, "h8", wt="wt-gone", now=100)
        q.reap_orphaned_targets({"wt-live"}, machine="m", grace_secs=0, now=200)
        assert q.get(t.id).completed_at == pytest.approx(200.0)


class TestLiveWorktrees:
    def test_returns_id_set(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture",
            _fake_run(0, '{"worktrees":[{"id":"wt-a"},{"id":"wt-b"}]}'))
        assert tracking.live_worktrees() == {"wt-a", "wt-b"}

    def test_supports_bare_array(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", _fake_run(0, '[{"id":"w1"}]')
        )
        assert tracking.live_worktrees() == {"w1"}

    def test_none_when_cli_absent(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", lambda *_a, **_k: None
        )
        assert tracking.live_worktrees() is None

    def test_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", _fake_run(2, "")
        )
        assert tracking.live_worktrees() is None

    def test_none_on_unparseable(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", _fake_run(0, "not json")
        )
        assert tracking.live_worktrees() is None

    def test_none_on_timeout(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", lambda *_a, **_k: None
        )
        assert tracking.live_worktrees() is None


class TestWorktreeDirectoryPresent:
    def test_true_when_row_present(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture",
            _fake_run(0, '[{"id": "wt", "status": "finalized"}]'),
        )
        assert tracking.worktree_directory_present("wt") is True

    def test_false_when_no_row(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", _fake_run(0, "[]")
        )
        assert tracking.worktree_directory_present("wt") is False

    def test_supports_object_wrapper(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture",
            _fake_run(0, '{"worktrees": [{"id": "wt"}]}'),
        )
        assert tracking.worktree_directory_present("wt") is True

    def test_none_when_cli_absent(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", lambda *_a, **_k: None
        )
        assert tracking.worktree_directory_present("wt") is None

    def test_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", _fake_run(2, "")
        )
        assert tracking.worktree_directory_present("wt") is None

    def test_none_on_unparseable(self, monkeypatch):
        monkeypatch.setattr(
            tracking, "run_agent_worktrees_capture", _fake_run(0, "not json")
        )
        assert tracking.worktree_directory_present("wt") is None

    def test_passes_project_as_a_leading_global_option(self, monkeypatch):
        captured = {}

        def fake_run(*args, **_kwargs):
            captured["args"] = args
            return _Proc(0, "[]")

        monkeypatch.setattr(tracking, "run_agent_worktrees_capture", fake_run)
        assert tracking.worktree_directory_present("wt", project="widget") is False
        assert captured["args"] == (
            "--project", "widget", "list", "--json",
            "--tracking-status", "all", "--include-other-platforms",
            "--worktree-id", "wt",
        )

    def test_omits_project_when_not_given(self, monkeypatch):
        captured = {}

        def fake_run(*args, **_kwargs):
            captured["args"] = args
            return _Proc(0, "[]")

        monkeypatch.setattr(tracking, "run_agent_worktrees_capture", fake_run)
        assert tracking.worktree_directory_present("wt") is False
        assert captured["args"] == (
            "list", "--json", "--tracking-status", "all",
            "--include-other-platforms", "--worktree-id", "wt",
        )
