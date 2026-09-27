"""Tests for worktree_status_audit (agent-worktrees-external-status-
accelerator effort, Phase 7 -- ongoing accuracy monitoring)."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
import types

from agent_worktrees import worktree_status_audit as wsa
from agent_worktrees import worktree_status_daemon
from agent_worktrees.worktree_status_cache import WorktreeStatusCache


def _write_cache_row(db_path, project, worktree_id, bundle):
    cache = WorktreeStatusCache(db_path)
    cache.get_or_refresh(project, worktree_id, force=False, compute=lambda: bundle)
    cache.close()


def test_module_imports_without_work_coalescing_singleton_installed():
    """Copilot review finding: this module (and `agent_worktrees.__main__`,
    which imports it eagerly for its CLI alias) must stay importable in an
    environment that never installed the vendored `work_coalescing_singleton`
    package (e.g. the standalone `worktree-manager` package's own test
    suite) -- the daemon-dependent pieces (`worktree_status_daemon`) must
    only ever be imported lazily inside the functions that actually need
    them, mirroring `__main__.py`'s own `cmd_status_monitor` and
    `session_tracking_cli.cmd_worktree_status_bundle`."""
    import subprocess
    import sys

    script = (
        "import builtins\n"
        "real_import = builtins.__import__\n"
        "def fake_import(name, *a, **k):\n"
        "    if name == 'work_coalescing_singleton' or name.startswith('work_coalescing_singleton.'):\n"
        "        raise ModuleNotFoundError(name)\n"
        "    return real_import(name, *a, **k)\n"
        "builtins.__import__ = fake_import\n"
        "import agent_worktrees.worktree_status_audit\n"
        "import agent_worktrees.__main__\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def _bundle(project, worktree_id, *, git_state=None, active=None):
    facts = {}
    if git_state is not None:
        facts["git_state"] = {"value": git_state, "confirmed": True, "observed_at": time.time()}
    if active is not None:
        facts["liveness"] = {"value": {"active": active}, "confirmed": True, "observed_at": time.time()}
    return {"project": project, "worktree_id": worktree_id, "facts": facts}


# -- read_cache_snapshot -----------------------------------------------------

def test_read_cache_snapshot_empty_when_file_missing(tmp_path):
    assert wsa.read_cache_snapshot(tmp_path / "nope.sqlite3") == []


def test_read_cache_snapshot_reads_persisted_rows(tmp_path):
    db_path = tmp_path / "cache.sqlite3"
    bundle = _bundle("proj", "wt1", git_state={"state": "clean"})
    _write_cache_row(db_path, "proj", "wt1", bundle)

    rows = wsa.read_cache_snapshot(db_path)
    assert len(rows) == 1
    assert rows[0]["project"] == "proj"
    assert rows[0]["worktree_id"] == "wt1"
    assert rows[0]["bundle"] == bundle
    assert isinstance(rows[0]["computed_at"], float)


def test_read_cache_snapshot_skips_a_corrupt_bundle_row(tmp_path):
    db_path = tmp_path / "cache.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE worktree_status_cache (project TEXT, worktree_id TEXT,"
        " bundle_json TEXT, computed_at REAL, demanded_at REAL)"
    )
    conn.execute(
        "INSERT INTO worktree_status_cache VALUES (?, ?, ?, ?, ?)",
        ("proj", "wt1", "not-json{{{", time.time(), time.time()),
    )
    conn.commit()
    conn.close()

    rows = wsa.read_cache_snapshot(db_path)
    assert len(rows) == 1
    assert rows[0]["bundle"] is None  # degraded, not raised/skipped entirely


# -- select_sample ------------------------------------------------------------

def test_select_sample_prefers_cache_rows_over_fallback(monkeypatch):
    cache_rows = [{"project": "p", "worktree_id": "a", "computed_at": 1, "demanded_at": 1}]
    monkeypatch.setattr(wsa, "_fallback_candidates", lambda: [("p", "should-not-be-used")])
    sample = wsa.select_sample(cache_rows, sample_size=5, rng=random.Random(1))
    assert sample == [("p", "a", cache_rows[0])]


def test_select_sample_falls_back_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(wsa, "_fallback_candidates", lambda: [("p", "a"), ("p", "b")])
    sample = wsa.select_sample([], sample_size=5, rng=random.Random(1))
    assert sorted(sample) == sorted([("p", "a", None), ("p", "b", None)])


def test_select_sample_respects_sample_size(monkeypatch):
    monkeypatch.setattr(
        wsa, "_fallback_candidates",
        lambda: [("p", str(i)) for i in range(20)],
    )
    sample = wsa.select_sample([], sample_size=3, rng=random.Random(1))
    assert len(sample) == 3


def test_select_sample_never_raises_on_a_non_positive_sample_size(monkeypatch):
    """Copilot review finding: `random.sample` raises `ValueError` for a
    negative count -- a misconfigured `--sample -1` (or `0`) must degrade
    to an empty sample instead of crashing, per this module's own "never
    raises" contract."""
    monkeypatch.setattr(
        wsa, "_fallback_candidates", lambda: [("p", str(i)) for i in range(5)],
    )
    assert wsa.select_sample([], sample_size=-1, rng=random.Random(1)) == []
    assert wsa.select_sample([], sample_size=0, rng=random.Random(1)) == []


# -- diffs ---------------------------------------------------------------

def test_diff_git_state_flags_a_real_mismatch():
    cached = _bundle("p", "wt1", git_state={"state": "clean", "ahead": 0})
    live = _bundle("p", "wt1", git_state={"state": "dirty", "ahead": 0})
    mismatches = wsa._diff_git_state(cached, live)
    assert len(mismatches) == 1
    assert mismatches[0].check == "git_state_accuracy"


def test_diff_git_state_no_mismatch_when_equal():
    cached = _bundle("p", "wt1", git_state={"state": "clean", "ahead": 0})
    live = _bundle("p", "wt1", git_state={"state": "clean", "ahead": 0})
    assert wsa._diff_git_state(cached, live) == []


def test_diff_git_state_skips_comparison_when_either_side_unconfirmed():
    cached = {"facts": {"git_state": {"value": {"state": "clean"}, "confirmed": False}}}
    live = _bundle("p", "wt1", git_state={"state": "dirty"})
    assert wsa._diff_git_state(cached, live) == []


def test_diff_liveness_flags_a_real_mismatch():
    cached = _bundle("p", "wt1", active=True)
    live = _bundle("p", "wt1", active=False)
    mismatches = wsa._diff_liveness(cached, live)
    assert len(mismatches) == 1
    assert mismatches[0].check == "liveness_accuracy"


def test_diff_liveness_no_mismatch_when_equal():
    cached = _bundle("p", "wt1", active=True)
    live = _bundle("p", "wt1", active=True)
    assert wsa._diff_liveness(cached, live) == []


# -- identity / freshness -------------------------------------------------

def test_check_identity_flags_a_mismatched_bundle():
    bundle = {"project": "p", "worktree_id": "wt2"}
    mismatches = wsa._check_identity("p", "wt1", bundle, source="cached")
    assert len(mismatches) == 1
    assert mismatches[0].check == "identity_consistency"


def test_check_identity_ok_when_matching():
    bundle = {"project": "p", "worktree_id": "wt1"}
    assert wsa._check_identity("p", "wt1", bundle, source="cached") == []


def test_check_identity_tolerates_a_missing_bundle():
    assert wsa._check_identity("p", "wt1", None, source="cached") == []


def test_check_freshness_flags_a_stale_entry():
    now = time.time()
    entry = {"computed_at": now - 10_000}
    mismatches = wsa._check_freshness(entry, now=now)
    assert len(mismatches) == 1
    assert mismatches[0].check == "cache_freshness_bounds"


def test_check_freshness_ok_for_a_recent_entry():
    now = time.time()
    entry = {"computed_at": now - 1}
    assert wsa._check_freshness(entry, now=now) == []


def test_check_freshness_tolerates_a_missing_entry():
    assert wsa._check_freshness(None, now=time.time()) == []


def test_check_freshness_flags_a_stale_entry_still_within_its_demand_window():
    """A demanded (demand_age < DEMAND_TTL_SECONDS), TTL-expired entry that
    the sweep hasn't refreshed is a real sweep malfunction -- must still be
    flagged."""
    from agent_worktrees.worktree_status_cache import DEMAND_TTL_SECONDS

    now = time.time()
    entry = {"computed_at": now - 10_000, "demanded_at": now - (DEMAND_TTL_SECONDS - 30)}
    mismatches = wsa._check_freshness(entry, now=now)
    assert len(mismatches) == 1
    assert mismatches[0].check == "cache_freshness_bounds"


def test_check_freshness_ok_when_demand_has_aged_out():
    """`WorktreeStatusCache.sweep_due` only refreshes an entry while it is
    still demanded -- once nothing has asked about this worktree within
    `DEMAND_TTL_SECONDS`, the sweep correctly stops keeping it warm (it
    will be evicted, not endlessly refreshed). A growing cache_age past
    that point is expected, not a sweep malfunction, and must not be
    flagged.

    Uses the cache's own exact `DEMAND_TTL_SECONDS` cutoff, not a padded
    one (Copilot review, 2026-09-21, round 2): padding this cutoff to
    tolerate the persisted `demanded_at`'s own narrow, self-correcting lag
    behind true in-memory demand (round 1's concern) would systematically
    reintroduce this exact false positive for every genuinely-expired row
    that ages through the padding window -- a worse, more frequent cost
    than the narrow race it would guard against."""
    from agent_worktrees.worktree_status_cache import DEMAND_TTL_SECONDS

    now = time.time()
    entry = {"computed_at": now - 10_000, "demanded_at": now - (DEMAND_TTL_SECONDS + 30)}
    assert wsa._check_freshness(entry, now=now) == []


def test_check_freshness_bound_widens_with_demanded_count_past_the_sweep_cap():
    """Regression (Copilot review, PR #3348): `WorktreeStatusCache.sweep_due`'s
    own `max_refresh_per_sweep` cap means a demanded entry is no longer
    guaranteed a refresh every single tick once the number of currently-
    demanded entries exceeds the cap -- excess entries are served round-
    robin, stalest-first, across successive ticks. A fixed, cap-unaware
    bound would false-positive under real load exactly proportional to how
    far demand exceeds the cap. `demanded_count` must widen the bound
    accordingly."""
    from agent_worktrees.worktree_status_cache import DEFAULT_MAX_REFRESH_PER_SWEEP
    from agent_worktrees.worktree_status_daemon import SWEEP_INTERVAL_SECONDS

    now = time.time()
    # An age that comfortably clears the base (demanded_count=1) bound but
    # falls within the extra slack a large demanded_count should grant.
    demanded_count = DEFAULT_MAX_REFRESH_PER_SWEEP * 5 + 1
    base_bound = wsa.DEFAULT_TTL_SECONDS + SWEEP_INTERVAL_SECONDS + wsa.FRESHNESS_SLACK_SECONDS
    age = base_bound + SWEEP_INTERVAL_SECONDS * 3
    entry = {"computed_at": now - age, "demanded_at": now}

    # With the default demanded_count=1, this age is already past the base
    # bound -- must be flagged.
    assert len(wsa._check_freshness(entry, now=now)) == 1

    # With enough demanded entries to make the sweep's own round-robin take
    # several extra ticks to reach this one, the SAME age must no longer be
    # flagged -- it's an expected, cap-driven wait, not a sweep malfunction.
    assert wsa._check_freshness(entry, now=now, demanded_count=demanded_count) == []


def test_count_actively_demanded_excludes_rows_whose_demand_has_aged_out():
    """Regression (Copilot review, PR #3348): `len(cache_rows)` counts every
    durable row, including ones `WorktreeStatusCache.sweep_due` will never
    refresh again -- rows whose `demanded_at` has already passed
    `DEMAND_TTL_SECONDS` are evicted, not refreshed. Counting those dormant
    rows would inflate the cap-based freshness bound for every genuinely
    active entry, and enough abandoned rows could let a real stuck sweep
    slip past the audit entirely."""
    from agent_worktrees.worktree_status_cache import DEMAND_TTL_SECONDS

    now = time.time()
    rows = [
        {"demanded_at": now},  # active
        {"demanded_at": now - (DEMAND_TTL_SECONDS - 1)},  # still active (barely)
        {"demanded_at": now - (DEMAND_TTL_SECONDS + 1)},  # dormant -- excluded
        {"demanded_at": now - 10_000},  # long dormant -- excluded
        {},  # malformed/missing -- conservatively counted as active
    ]
    assert wsa._count_actively_demanded(rows, now=now) == 3


# -- audit_one -------------------------------------------------------------

def test_audit_one_clean_when_cache_matches_live(monkeypatch):
    bundle = _bundle("p", "wt1", git_state={"state": "clean"}, active=True)
    monkeypatch.setattr(wsa.worktree_status_compute, "compute", lambda project, wt_id: bundle)
    entry = {"project": "p", "worktree_id": "wt1", "bundle": bundle,
              "computed_at": time.time(), "demanded_at": time.time()}
    result = wsa.audit_one("p", "wt1", entry, now=time.time())
    assert result.ok
    assert result.had_cache_entry is True
    assert result.error is None


def test_audit_one_reports_a_git_state_mismatch(monkeypatch):
    cached = _bundle("p", "wt1", git_state={"state": "clean"})
    live = _bundle("p", "wt1", git_state={"state": "dirty"})
    monkeypatch.setattr(wsa.worktree_status_compute, "compute", lambda project, wt_id: live)
    entry = {"project": "p", "worktree_id": "wt1", "bundle": cached,
              "computed_at": time.time(), "demanded_at": time.time()}
    result = wsa.audit_one("p", "wt1", entry, now=time.time())
    assert not result.ok
    assert any(m.check == "git_state_accuracy" for m in result.mismatches)


def test_audit_one_records_a_compute_exception_as_its_own_error(monkeypatch):
    def _boom(project, wt_id):
        raise ValueError("no such worktree")

    monkeypatch.setattr(wsa.worktree_status_compute, "compute", _boom)
    result = wsa.audit_one("p", "wt1", None, now=time.time())
    assert not result.ok
    assert result.error == "ValueError: no such worktree"
    assert result.had_cache_entry is False


def test_audit_one_never_diffs_against_a_cold_entry(monkeypatch):
    """No cache entry at all (a fallback-sampled worktree) -- nothing to
    diff against, only ground truth itself, so no mismatches possible from
    the diff steps."""
    live = _bundle("p", "wt1", git_state={"state": "dirty"}, active=False)
    monkeypatch.setattr(wsa.worktree_status_compute, "compute", lambda project, wt_id: live)
    result = wsa.audit_one("p", "wt1", None, now=time.time())
    assert result.ok
    assert result.cache_age_seconds is None
    assert result.demand_age_seconds is None


# -- check_daemon_liveness -------------------------------------------------

def test_daemon_liveness_no_lock_file(tmp_path):
    """No probe, no ensure_monitor, no lock at all: without a real
    request there is nothing that actually failed -- only "no one to
    ask" -- so this must never be reported as `responsive=False` (which
    `cmd_worktree_status_audit` would otherwise turn into a failing exit
    code for the accelerator's own healthy idle-exited resting state)."""
    liveness = wsa.check_daemon_liveness(tmp_path / "status-monitor.lock")
    assert liveness.lock_present is False
    assert liveness.responsive is None


def test_daemon_liveness_stale_lock_reports_not_live(tmp_path):
    from agent_worktrees import locks

    lock_path = tmp_path / "status-monitor.lock"
    # An implausible pid that's essentially guaranteed dead/never existed.
    locks.write_lock(lock_path, pid=2**30 - 1)
    liveness = wsa.check_daemon_liveness(lock_path)
    assert liveness.lock_present is True
    assert liveness.rendezvous_present is False
    assert liveness.responsive is None


def test_daemon_liveness_no_probe_boots_and_waits_when_ensure_monitor_given(tmp_path):
    """Copilot review round 4 on PR #3206: `run_audit` passes `probe=None`
    whenever the sample is empty (e.g. an empty cache) -- exactly the
    `cache_row_count: 0` scenario that originally motivated this whole
    redesign. The no-probe path must still give an idle-exited monitor
    the same boot-and-wait chance a real probe would, when `ensure_monitor`
    is available, instead of taking an immediate empty snapshot."""
    lock_path = tmp_path / "status-monitor.lock"
    calls = []
    liveness = wsa.check_daemon_liveness(
        lock_path, probe=None, ensure_monitor=lambda: calls.append(1) or True,
        boot_wait_s=0.2,
    )
    assert calls == [1]
    assert liveness.responsive is None
    assert liveness.lock_present is False


def test_daemon_liveness_no_probe_boot_wait_observes_a_delayed_lock(tmp_path):
    """Copilot review round 5 on PR #3206: the prior no-probe boot-wait
    test only checked that `ensure_monitor` was invoked -- it never
    actually verified the poll loop itself observes a lock that lands
    *during* the wait window (the same gap the probed-path test already
    closed for that side of the fix). Publish the lock from a background
    thread after a real delay so this exercises the poll loop, not just
    the call to `ensure_monitor`."""
    import threading

    from agent_worktrees import locks

    lock_path = tmp_path / "status-monitor.lock"

    def _write_lock_after_delay():
        time.sleep(0.1)
        locks.write_lock(lock_path, extra={
            "worktree_status_endpoint": "127.0.0.1:1", "worktree_status_token": "tok",
        })

    def _ensure_monitor():
        threading.Thread(target=_write_lock_after_delay, daemon=True).start()
        return True

    assert not lock_path.exists()
    liveness = wsa.check_daemon_liveness(
        lock_path, probe=None, ensure_monitor=_ensure_monitor, boot_wait_s=1.0,
    )
    assert liveness.lock_present is True
    assert liveness.rendezvous_present is True
    assert liveness.responsive is None


def test_daemon_liveness_no_probe_reports_unknown_responsiveness(tmp_path):
    from agent_worktrees import locks

    lock_path = tmp_path / "status-monitor.lock"
    server = worktree_status_daemon.start_server(lambda kind, payload: {"ok": True})
    server.start()
    try:
        locks.write_lock(lock_path, extra=worktree_status_daemon.rendezvous_fields(server))
        liveness = wsa.check_daemon_liveness(lock_path, probe=None)
        assert liveness.lock_present is True
        assert liveness.rendezvous_present is True
        assert liveness.responsive is None
    finally:
        server.close()


def test_daemon_liveness_probe_succeeds_against_a_live_daemon(tmp_path):
    from agent_worktrees import locks

    lock_path = tmp_path / "status-monitor.lock"
    server = worktree_status_daemon.start_server(
        lambda kind, payload: {"worktree_id": payload["worktree_id"]}
    )
    server.start()
    try:
        locks.write_lock(lock_path, extra=worktree_status_daemon.rendezvous_fields(server))
        liveness = wsa.check_daemon_liveness(lock_path, probe=("proj", "wt1"))
        assert liveness.responsive is True
    finally:
        server.close()


def test_daemon_liveness_probe_reports_unresponsive_when_daemon_is_down(tmp_path):
    lock_path = tmp_path / "status-monitor.lock"
    liveness = wsa.check_daemon_liveness(lock_path, probe=("proj", "wt1"))
    assert liveness.lock_present is False
    assert liveness.responsive is False


def test_daemon_liveness_calls_ensure_monitor_when_nothing_is_reachable(tmp_path):
    """Copilot review-driven redesign: a real caller always reaches the
    daemon via `status_with_boot`, which triggers `ensure_monitor` and
    waits when no endpoint is currently reachable -- an idle-exited
    resident monitor (this system's own resting state between infrequent
    callers) is not a fault a bare, never-boots probe would otherwise
    misreport as `responsive: false`."""
    lock_path = tmp_path / "status-monitor.lock"
    calls = []
    wsa.check_daemon_liveness(
        lock_path, probe=("proj", "wt1"), ensure_monitor=lambda: calls.append(1) or True,
        boot_wait_s=0.2,
    )
    assert calls == [1]


def test_daemon_liveness_ensure_monitor_boot_is_picked_up_within_the_wait(tmp_path, monkeypatch):
    """The full boot-and-wait path: `ensure_monitor` starts a real server
    but only publishes the lock after a short delay on a background
    thread (simulating a resident monitor's real spawn latency, not an
    immediate write that would pass even without any actual polling) --
    `status_with_boot`'s own poll loop must still pick it up before its
    wait expires, exactly as any real caller's first request after an
    idle-exit would."""
    import threading

    from agent_worktrees import locks

    lock_path = tmp_path / "status-monitor.lock"
    server = worktree_status_daemon.start_server(
        lambda kind, payload: {"worktree_id": payload["worktree_id"]}
    )
    server.start()
    try:
        def _write_lock_after_delay():
            time.sleep(0.3)
            locks.write_lock(lock_path, extra=worktree_status_daemon.rendezvous_fields(server))

        def _ensure_monitor():
            threading.Thread(target=_write_lock_after_delay, daemon=True).start()
            return True

        assert not lock_path.exists()
        liveness = wsa.check_daemon_liveness(
            lock_path, probe=("proj", "wt1"), ensure_monitor=_ensure_monitor,
        )
        assert liveness.responsive is True
        assert liveness.lock_present is True
        assert liveness.rendezvous_present is True
    finally:
        server.close()


def test_daemon_liveness_stale_lock_with_probe_reports_unresponsive_not_rendezvous(tmp_path):
    """Copilot review round 2 on PR #3206: a stale (dead-owner) lock that
    still carries old, syntactically valid rendezvous fields must not be
    reported as `rendezvous_present=True` just because the boot-and-wait
    dial step correctly rejected it and fell back -- the post-probe
    snapshot re-read has to apply the exact same `lock_is_live` check the
    no-probe static-read branch already applies, or a dead monitor's
    leftover lock would look like a live, reachable rendezvous that
    merely failed to answer this one request."""
    from agent_worktrees import locks

    lock_path = tmp_path / "status-monitor.lock"
    # An implausible pid that's essentially guaranteed dead/never existed,
    # but with otherwise well-formed rendezvous fields.
    locks.write_lock(
        lock_path, pid=2**30 - 1,
        extra={"worktree_status_endpoint": "127.0.0.1:1", "worktree_status_token": "x"},
    )
    ensure_monitor_calls = []
    liveness = wsa.check_daemon_liveness(
        lock_path, probe=("proj", "wt1"),
        ensure_monitor=lambda: ensure_monitor_calls.append(1) or True,
        boot_wait_s=0.2,
    )
    assert ensure_monitor_calls == [1]
    assert liveness.responsive is False
    assert liveness.lock_present is True
    assert liveness.rendezvous_present is False


# -- run_audit / report_to_dict / telemetry log --------------------------

def test_run_audit_writes_a_telemetry_log_line(tmp_path, monkeypatch):
    runtime_home = tmp_path
    bundle = _bundle("p", "wt1", git_state={"state": "clean"})
    _write_cache_row(wsa.cache_db_path(runtime_home), "p", "wt1", bundle)
    monkeypatch.setattr(wsa.worktree_status_compute, "compute", lambda project, wt_id: bundle)

    log_path = tmp_path / "audit.jsonl"
    report = wsa.run_audit(
        runtime_home=runtime_home, sample_size=5, log_path=log_path, rng=random.Random(1),
    )
    assert report.cache_row_count == 1
    assert report.mismatch_count == 0
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["sampled_count"] == 1


def test_run_audit_appends_one_line_per_call(tmp_path, monkeypatch):
    runtime_home = tmp_path
    monkeypatch.setattr(
        wsa.worktree_status_compute, "compute",
        lambda project, wt_id: _bundle(project, wt_id),
    )
    log_path = tmp_path / "audit.jsonl"
    for _ in range(3):
        wsa.run_audit(runtime_home=runtime_home, sample_size=1, log_path=log_path)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_run_audit_never_raises_when_log_write_fails(tmp_path, monkeypatch):
    """Copilot review contract: telemetry-log write failure must never
    surface past run_audit -- best-effort only."""
    runtime_home = tmp_path
    monkeypatch.setattr(
        wsa.worktree_status_compute, "compute", lambda project, wt_id: _bundle(project, wt_id)
    )
    # A log path whose parent cannot be created (a file where a directory
    # is expected) -- `mkdir` raises, `_append_log` must swallow it.
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    log_path = blocked / "audit.jsonl"
    report = wsa.run_audit(runtime_home=runtime_home, sample_size=1, log_path=log_path)
    assert report is not None  # no exception propagated


def test_report_to_dict_shape(tmp_path):
    report = wsa.AuditReport(
        timestamp=123.0,
        sampled=[
            wsa.WorktreeAudit(
                project="p", worktree_id="wt1", had_cache_entry=True,
                cache_age_seconds=1.0, demand_age_seconds=1.0,
                mismatches=[wsa.FieldMismatch(check="git_state_accuracy", detail="x")],
            )
        ],
        daemon=wsa.DaemonLiveness(lock_present=True, rendezvous_present=True, responsive=True),
        cache_row_count=1,
    )
    payload = wsa.report_to_dict(report)
    assert payload["version"] == 1
    assert payload["mismatch_count"] == 1
    assert payload["entries"][0]["ok"] is False
    assert payload["entries"][0]["mismatches"][0]["check"] == "git_state_accuracy"


# -- CLI --------------------------------------------------------------------

def test_cmd_worktree_status_audit_exit_code_clean(tmp_path, monkeypatch):
    fake_core = types.SimpleNamespace(
        _aw_runtime_home=lambda: tmp_path,
        _json_output=lambda payload: None,
        _status_monitor_enabled=lambda: True,
        _ensure_status_monitor=lambda: True,
    )
    monkeypatch.setattr(wsa, "_core", lambda: fake_core)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_aw_runtime_home", fake_core._aw_runtime_home)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_status_monitor_enabled", fake_core._status_monitor_enabled)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_ensure_status_monitor", fake_core._ensure_status_monitor)
    bundle = _bundle("p", "wt1", git_state={"state": "clean"})
    _write_cache_row(wsa.cache_db_path(tmp_path), "p", "wt1", bundle)
    monkeypatch.setattr(wsa.worktree_status_compute, "compute", lambda project, wt_id: bundle)
    monkeypatch.setattr(
        wsa, "check_daemon_liveness",
        lambda lock_path, probe=None, ensure_monitor=None: wsa.DaemonLiveness(
            lock_present=True, rendezvous_present=True, responsive=True,
        ),
    )
    rc = wsa.cmd_worktree_status_audit(
        argparse.Namespace(sample=1, log_path=None, no_log=True, seed=1, json=True)
    )
    assert rc == 0


def test_cmd_worktree_status_audit_passes_ensure_monitor_when_enabled(tmp_path, monkeypatch):
    """Copilot review round 3 on PR #3206: the `ensure_monitor` opt-out
    wiring itself (resolved from `core._status_monitor_enabled()`/
    `core._ensure_status_monitor`) had no CLI-level regression coverage --
    a regression that always boots, or never does regardless of the
    opt-out, would still pass every other CLI test here since none of
    them inspect what `run_audit` actually received."""
    sentinel_ensure_monitor = lambda: True  # noqa: E731 -- identity marker
    fake_core = types.SimpleNamespace(
        _aw_runtime_home=lambda: tmp_path,
        _json_output=lambda payload: None,
        _status_monitor_enabled=lambda: True,
        _ensure_status_monitor=sentinel_ensure_monitor,
    )
    monkeypatch.setattr(wsa, "_core", lambda: fake_core)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_aw_runtime_home", fake_core._aw_runtime_home)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_status_monitor_enabled", fake_core._status_monitor_enabled)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_ensure_status_monitor", fake_core._ensure_status_monitor)
    captured = {}
    real_run_audit = wsa.run_audit

    def _spy_run_audit(**kwargs):
        captured["ensure_monitor"] = kwargs.get("ensure_monitor")
        return real_run_audit(**kwargs)

    monkeypatch.setattr(wsa, "run_audit", _spy_run_audit)
    # This test only asserts wiring (what `run_audit` received) -- stub
    # out the actual daemon probe so it doesn't pay the real boot-wait
    # window (the sample is empty here, so `probe=None`, and a sentinel
    # `ensure_monitor` that never publishes a lock would otherwise make
    # the real no-probe wait loop run to its full timeout).
    monkeypatch.setattr(
        wsa, "check_daemon_liveness",
        lambda lock_path, probe=None, ensure_monitor=None, boot_wait_s=None: wsa.DaemonLiveness(
            lock_present=False, rendezvous_present=False, responsive=None,
        ),
    )
    wsa.cmd_worktree_status_audit(
        argparse.Namespace(sample=1, log_path=None, no_log=True, seed=1, json=True)
    )
    assert captured["ensure_monitor"] is sentinel_ensure_monitor


def test_cmd_worktree_status_audit_omits_ensure_monitor_when_disabled(tmp_path, monkeypatch):
    """The opt-out's other half: when the resident monitor is disabled
    (``AGENT_WORKTREES_STATUS_MONITOR=0``, surfaced here as
    ``_status_monitor_enabled() -> False``), `run_audit` must receive
    ``ensure_monitor=None`` -- never the real boot callable -- so the
    audit can't spawn a monitor an operator deliberately turned off."""
    fake_core = types.SimpleNamespace(
        _aw_runtime_home=lambda: tmp_path,
        _json_output=lambda payload: None,
        _status_monitor_enabled=lambda: False,
        _ensure_status_monitor=lambda: True,
    )
    monkeypatch.setattr(wsa, "_core", lambda: fake_core)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_aw_runtime_home", fake_core._aw_runtime_home)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_status_monitor_enabled", fake_core._status_monitor_enabled)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_ensure_status_monitor", fake_core._ensure_status_monitor)
    captured = {}
    real_run_audit = wsa.run_audit

    def _spy_run_audit(**kwargs):
        captured["ensure_monitor"] = kwargs.get("ensure_monitor")
        return real_run_audit(**kwargs)

    monkeypatch.setattr(wsa, "run_audit", _spy_run_audit)
    wsa.cmd_worktree_status_audit(
        argparse.Namespace(sample=1, log_path=None, no_log=True, seed=1, json=True)
    )
    assert captured["ensure_monitor"] is None


def test_cmd_worktree_status_audit_exit_code_nonzero_on_mismatch(tmp_path, monkeypatch):
    fake_core = types.SimpleNamespace(
        _aw_runtime_home=lambda: tmp_path,
        _json_output=lambda payload: None,
        _status_monitor_enabled=lambda: False,
        _ensure_status_monitor=lambda: True,
    )
    monkeypatch.setattr(wsa, "_core", lambda: fake_core)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_aw_runtime_home", fake_core._aw_runtime_home)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_status_monitor_enabled", fake_core._status_monitor_enabled)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_ensure_status_monitor", fake_core._ensure_status_monitor)
    cached = _bundle("p", "wt1", git_state={"state": "clean"})
    live = _bundle("p", "wt1", git_state={"state": "dirty"})
    _write_cache_row(wsa.cache_db_path(tmp_path), "p", "wt1", cached)
    monkeypatch.setattr(wsa.worktree_status_compute, "compute", lambda project, wt_id: live)
    rc = wsa.cmd_worktree_status_audit(
        argparse.Namespace(sample=5, log_path=None, no_log=True, seed=1, json=True)
    )
    assert rc == 1


def test_cmd_worktree_status_audit_respects_no_log(tmp_path, monkeypatch):
    fake_core = types.SimpleNamespace(
        _aw_runtime_home=lambda: tmp_path,
        _json_output=lambda payload: None,
        _status_monitor_enabled=lambda: False,
        _ensure_status_monitor=lambda: True,
    )
    monkeypatch.setattr(wsa, "_core", lambda: fake_core)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_aw_runtime_home", fake_core._aw_runtime_home)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_status_monitor_enabled", fake_core._status_monitor_enabled)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_ensure_status_monitor", fake_core._ensure_status_monitor)
    monkeypatch.setattr(
        wsa.worktree_status_compute, "compute", lambda project, wt_id: _bundle(project, wt_id)
    )
    wsa.cmd_worktree_status_audit(
        argparse.Namespace(sample=1, log_path=None, no_log=True, seed=1, json=True)
    )
    assert not (tmp_path / wsa.DEFAULT_LOG_FILENAME).exists()


def test_cmd_worktree_status_audit_uses_explicit_log_path(tmp_path, monkeypatch):
    fake_core = types.SimpleNamespace(
        _aw_runtime_home=lambda: tmp_path,
        _json_output=lambda payload: None,
        _status_monitor_enabled=lambda: False,
        _ensure_status_monitor=lambda: True,
    )
    monkeypatch.setattr(wsa, "_core", lambda: fake_core)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_aw_runtime_home", fake_core._aw_runtime_home)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_status_monitor_enabled", fake_core._status_monitor_enabled)
    monkeypatch.setattr(wsa.status_monitor_runtime, "_ensure_status_monitor", fake_core._ensure_status_monitor)
    monkeypatch.setattr(
        wsa.worktree_status_compute, "compute", lambda project, wt_id: _bundle(project, wt_id)
    )
    custom_log = tmp_path / "custom" / "audit.jsonl"
    wsa.cmd_worktree_status_audit(
        argparse.Namespace(
            sample=1, log_path=str(custom_log), no_log=False, seed=1, json=True,
        )
    )
    assert custom_log.exists()
