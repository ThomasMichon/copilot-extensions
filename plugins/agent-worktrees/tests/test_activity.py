"""Tests for the worktree activity log."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_worktrees import activity, handoff_trace


def _claim_prune_marker_in_subprocess(args: tuple[str, object]) -> bool:
    """Module-level (picklable) target for a real multi-process race test --
    see test_claim_prune_marker_atomic_under_real_multiprocess_race. Waits on
    a shared barrier first so every worker's claim attempt genuinely
    overlaps instead of running strictly one after another."""
    log_str, barrier = args
    barrier.wait()
    return activity._claim_prune_marker(Path(log_str))


def _prune_in_subprocess(args: tuple[str, object]) -> int:
    """Module-level (picklable) target for a real multi-process concurrent-
    rewrite test -- see test_prune_concurrent_invocations_never_corrupt_the_log."""
    log_str, barrier = args
    barrier.wait()
    return activity._prune(Path(log_str), retention_days=7)


@pytest.fixture
def patch_install_dir(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the activity log into a tmp install dir."""
    monkeypatch.setattr(
        "agent_worktrees.config.install_dir", lambda: tmp_path / ".agent-worktrees"
    )
    return tmp_path / ".agent-worktrees"


@pytest.fixture
def patch_active_project(monkeypatch):
    """Resolve an in-process active project for the durable trace store."""
    monkeypatch.setattr("agent_worktrees.config.active_project", lambda: "proj-a")


def test_log_event_writes_jsonl(patch_install_dir: Path):
    activity.log_event(
        "worktree_created", worktree_id="wt-1", branch="worktree/wt-1"
    )
    events = activity.read_events()
    assert len(events) == 1
    rec = events[0]
    assert rec["event"] == "worktree_created"
    assert rec["worktree_id"] == "wt-1"
    assert rec["branch"] == "worktree/wt-1"
    # session_id present (None) but no spurious extras
    assert rec["session_id"] is None
    assert "ts" in rec and "pid" in rec and "host" in rec


def test_log_event_drops_none_fields(patch_install_dir: Path):
    activity.log_event("session_started", worktree_id="wt-1", reason=None)
    rec = activity.read_events()[0]
    assert "reason" not in rec


def test_read_events_preserves_shell_written_boot_trace_records(
    patch_install_dir: Path,
):
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '{"ts":"2026-09-22T21:00:00+00:00","event":"boot_trace","plugin":"agent-worktrees","phase":"dispatch","t_ms":1790112610123,"pid":123,"host":"test-host","source":"launcher","path":"fast"}\n',
        encoding="utf-8",
    )

    rec = activity.read_events()[0]
    assert rec["event"] == "boot_trace"
    assert rec["plugin"] == "agent-worktrees"
    assert rec["phase"] == "dispatch"
    assert rec["source"] == "launcher"
    assert rec["path"] == "fast"


def test_log_event_stamps_known_handoff_stage(patch_install_dir: Path):
    activity.log_event("handoff_requested", worktree_id="wt-1", session_id="s1")
    rec = activity.read_events()[0]
    assert rec["stage"] == 6
    assert rec["stage_name"] == "handoff_triggered"


def test_log_event_normalizes_universal_handoff_fields(patch_install_dir: Path):
    activity.log_event(
        "handoff_requested",
        worktree_id="wt-1",
        session_id="s1",
        handoff_id="task-1",
    )
    rec = activity.read_events()[0]
    assert rec["handoff_token"] == "task-1"
    assert rec["predecessor_session_id"] == "s1"
    assert rec["successor_session_id"] is None


def test_log_event_maps_existing_events_to_their_stage(patch_install_dir: Path):
    activity.log_event(
        "handoff_cutover_claim", worktree_id="wt-1", outcome="acquired"
    )
    activity.log_event("handoff_successor_spawn_started", worktree_id="wt-1")
    activity.log_event("handoff_cutover_spawn", worktree_id="wt-1")
    activity.log_event(
        "handoff_predecessor_retire", worktree_id="wt-1", outcome="gone"
    )
    events = activity.read_events()
    stages = [(e["event"], e["stage"], e["stage_name"]) for e in events]
    assert stages == [
        ("handoff_cutover_claim", 7, "handoff_host_acknowledged"),
        ("handoff_successor_spawn_started", 8, "handoff_successor_spawn_started"),
        ("handoff_cutover_spawn", 8, "handoff_successor_spawn_started"),
        (
            "handoff_predecessor_retire",
            11,
            "handoff_pickup_confirmed_predecessor_closing",
        ),
    ]


def test_log_event_stamps_spawn_failure_as_stage_8(patch_install_dir: Path):
    """A failed spawn still lands stage 8 -- distinguishable from a killed
    spawn (no stage-8 event at all) by the terminal `_failed` event name."""
    activity.log_event("handoff_successor_spawn_started", worktree_id="wt-1")
    activity.log_event(
        "handoff_successor_spawn_failed", worktree_id="wt-1", error="boom"
    )
    events = activity.read_events()
    assert [e["event"] for e in events] == [
        "handoff_successor_spawn_started",
        "handoff_successor_spawn_failed",
    ]
    for rec in events:
        assert rec["stage"] == 8
        assert rec["stage_name"] == "handoff_successor_spawn_started"


def test_log_event_does_not_stamp_a_failed_or_duplicate_claim(
    patch_install_dir: Path,
):
    activity.log_event(
        "handoff_cutover_claim", worktree_id="wt-1", outcome="already-claimed"
    )
    activity.log_event("handoff_cutover_claim", worktree_id="wt-1", outcome="error")
    for rec in activity.read_events():
        assert "stage" not in rec
        assert "stage_name" not in rec


def test_log_event_does_not_stamp_an_unretired_predecessor(patch_install_dir: Path):
    activity.log_event(
        "handoff_predecessor_retire", worktree_id="wt-1", outcome="left-running"
    )
    activity.log_event(
        "handoff_predecessor_retire", worktree_id="wt-1", outcome="identity-mismatch"
    )
    for rec in activity.read_events():
        assert "stage" not in rec
        assert "stage_name" not in rec



def test_log_event_reserves_stage_fields_against_caller_override(
    patch_install_dir: Path,
):
    activity.log_event(
        "handoff_requested",
        worktree_id="wt-1",
        stage=999,
        stage_name="not-a-real-stage",
    )
    rec = activity.read_events()[0]
    assert rec["stage"] == 6
    assert rec["stage_name"] == "handoff_triggered"


def test_log_event_preserves_caller_stage_fields_on_unmapped_events(
    patch_install_dir: Path,
):
    """A custom/unmapped event is untouched -- only a *mapped* event's stamp
    is reserved/gated (#3994401488)."""
    activity.log_event(
        "some_custom_event",
        worktree_id="wt-1",
        stage="custom-stage-value",
        stage_name="custom-stage-name",
    )
    rec = activity.read_events()[0]
    assert rec["stage"] == "custom-stage-value"
    assert rec["stage_name"] == "custom-stage-name"


def test_log_event_omits_stage_fields_for_unmapped_events(patch_install_dir: Path):
    activity.log_event("mux_failed", worktree_id="wt-1")
    rec = activity.read_events()[0]
    assert "stage" not in rec
    assert "stage_name" not in rec


def test_read_events_filters(patch_install_dir: Path):
    activity.log_event("worktree_created", worktree_id="wt-1")
    activity.log_event("session_started", worktree_id="wt-1", session_id="s1")
    activity.log_event("worktree_created", worktree_id="wt-2")

    assert len(activity.read_events(worktree_id="wt-1")) == 2
    assert len(activity.read_events(event="worktree_created")) == 2
    assert len(activity.read_events(worktree_id="wt-2", event="worktree_created")) == 1


def test_read_events_reuses_the_cached_parse_across_distinct_filters(
    patch_install_dir: Path, monkeypatch,
):
    """copilot-extensions#3751's own diagnosis, same shape, different call
    path: the resident status-monitor's handoff-retire sweep
    (``_pending_handoff_retire_requests``) calls ``read_events`` twice per
    worktree, every sweep tick, with different ``event=`` filters -- both
    calls must hit the SAME cached parse of this machine-global log, not
    re-read + re-``json.loads`` the whole file twice."""
    activity.log_event("worktree_created", worktree_id="wt-1")
    activity.log_event("handoff_cutover_spawn", worktree_id="wt-1")

    calls: list[Path] = []
    real_parse_all = activity._parse_all_events

    def _spy(path: Path):
        calls.append(path)
        return real_parse_all(path)

    monkeypatch.setattr(activity, "_parse_all_events", _spy)
    activity.read_events(worktree_id="wt-1", event="handoff_cutover_spawn")
    activity.read_events(worktree_id="wt-1", event="handoff_predecessor_retire")
    assert len(calls) == 1, "a second call with a different filter must still be a cache hit"

    activity.log_event("handoff_predecessor_retire", worktree_id="wt-1")
    activity.read_events(worktree_id="wt-1", event="handoff_predecessor_retire")
    assert len(calls) == 2, "an appended event must force exactly one re-parse"


def test_read_events_limit_returns_most_recent(patch_install_dir: Path):
    for i in range(5):
        activity.log_event("session_started", worktree_id=f"wt-{i}")
    recent = activity.read_events(limit=2)
    assert len(recent) == 2
    assert recent[0]["worktree_id"] == "wt-3"
    assert recent[1]["worktree_id"] == "wt-4"


def test_read_events_missing_file(patch_install_dir: Path):
    assert activity.read_events() == []


def test_parse_since_durations():
    now = datetime.now(timezone.utc)
    got = activity.parse_since("2d")
    assert got is not None
    assert abs((now - got) - timedelta(days=2)) < timedelta(seconds=5)
    assert activity.parse_since("30m") is not None
    assert activity.parse_since("1w") is not None
    assert activity.parse_since("garbage") is None
    assert activity.parse_since("") is None


def test_parse_since_iso():
    got = activity.parse_since("2026-06-09")
    assert got is not None
    assert got.year == 2026 and got.month == 6 and got.day == 9


def test_since_filter_excludes_old(patch_install_dir: Path, monkeypatch):
    # Write one old and one new event by controlling the timestamp.
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        f'{{"ts": "{old_ts}", "event": "x", "worktree_id": "wt-old"}}\n'
        f'{{"ts": "{new_ts}", "event": "x", "worktree_id": "wt-new"}}\n'
    )
    since = activity.parse_since("2d")
    got = activity.read_events(since=since)
    assert [r["worktree_id"] for r in got] == ["wt-new"]


def test_prune_drops_old_lines(patch_install_dir: Path):
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        f'{{"ts": "{old_ts}", "event": "x", "worktree_id": "old"}}\n'
        f'{{"ts": "{new_ts}", "event": "x", "worktree_id": "new"}}\n'
    )
    kept = activity._prune(log, retention_days=7)
    assert kept == 1
    remaining = activity.read_events()
    assert len(remaining) == 1
    assert remaining[0]["worktree_id"] == "new"


def test_prune_invalidates_this_path_s_jsonl_cache_entry(patch_install_dir: Path, monkeypatch):
    """``_prune()`` rewrites ``activity.jsonl`` in place at the same path --
    on a filesystem with coarse mtime resolution, the rewritten file can
    coincidentally reproduce the exact ``(mtime_ns, size)`` stamp the pre-
    prune content was cached under. ``_prune`` must invalidate that path's
    jsonl_cache entry itself rather than rely on the stamp always differing
    (see jsonl_cache's own ``invalidate()`` docstring)."""
    from agent_worktrees import jsonl_cache

    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        f'{{"ts": "{old_ts}", "event": "x", "worktree_id": "old"}}\n'
        f'{{"ts": "{new_ts}", "event": "x", "worktree_id": "new"}}\n'
    )

    calls: list[Path] = []
    real_invalidate = jsonl_cache.invalidate

    def _spy(path: Path):
        calls.append(path)
        return real_invalidate(path)

    monkeypatch.setattr(jsonl_cache, "invalidate", _spy)
    activity._prune(log, retention_days=7)
    assert calls == [log], "_prune must invalidate exactly this path's cache entry"


def test_dispatch_background_prune_reaps_the_child_without_blocking(monkeypatch):
    """A long-lived caller (the picker, a resident daemon) must never
    accumulate zombie/unreaped children from repeated dispatches: the
    ``Popen`` handle is retained and waited on from a background thread,
    not discarded."""
    import threading
    import time

    wait_called = threading.Event()
    seen_kwargs: dict = {}

    class FakeProc:
        def wait(self):
            wait_called.set()

    def _fake_popen(*a, **k):
        seen_kwargs.update(k)
        return FakeProc()

    monkeypatch.setattr(activity.subprocess, "Popen", _fake_popen)

    start = time.monotonic()
    activity._dispatch_background_prune(Path("/does/not/matter/activity.jsonl"))
    elapsed = time.monotonic() - start

    assert elapsed < 1, "dispatch itself must return immediately"
    assert wait_called.wait(timeout=2), "the child must be reaped via a background thread"
    # Never the caller's cwd -- this detached worker may outlive the repo/
    # worktree checkout log_event() happened to be called from.
    assert seen_kwargs.get("cwd") == os.path.expanduser("~")


def test_prune_concurrent_invocations_never_corrupt_the_log(tmp_path: Path):
    """Adjacent debounce windows are allowed to each dispatch their own
    worker (see _claim_prune_marker's grace window), so two real `_prune()`
    calls against the same log can genuinely overlap. Each must use its own
    temp file (not a fixed shared name) -- released together from a shared
    barrier so the overlap is real, not sequential, the final file must
    still be entirely valid, retention-filtered JSONL, never interleaved or
    truncated garbage from two processes racing on the same temp path."""
    import concurrent.futures
    import multiprocessing

    log = tmp_path / "activity.jsonl"
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        f'{{"ts": "{old_ts}", "event": "x", "worktree_id": "old"}}\n'
        f'{{"ts": "{new_ts}", "event": "x", "worktree_id": "new"}}\n'
    )

    n = 6
    with multiprocessing.Manager() as manager:
        barrier = manager.Barrier(n)
        with concurrent.futures.ProcessPoolExecutor(max_workers=n) as pool:
            list(pool.map(_prune_in_subprocess, [(str(log), barrier)] * n))

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["worktree_id"] == "new"


def test_log_event_never_prunes_inline(patch_install_dir: Path, monkeypatch):
    """A large log must dispatch a background worker, never rewrite inline.

    log_event() can be called mid-interaction (a picker action, a submenu
    open); a synchronous multi-second rewrite on that path would freeze the
    caller between keypresses. This proves log_event() never calls the
    actual rewrite (`_prune`) itself once the size threshold is crossed --
    only the cheap, fire-and-forget dispatch.
    """
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * (activity._PRUNE_SIZE_BYTES + 1))

    prune_calls = []
    dispatch_calls = []
    monkeypatch.setattr(activity, "_prune", lambda *a, **k: prune_calls.append((a, k)) or 0)
    monkeypatch.setattr(
        activity, "_dispatch_background_prune", lambda path: dispatch_calls.append(path)
    )

    activity.log_event("worktree_created", worktree_id="wt-1")

    assert prune_calls == []
    assert dispatch_calls == [log]


def test_maybe_prune_dispatches_once_per_debounce_window(patch_install_dir: Path, monkeypatch):
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * (activity._PRUNE_SIZE_BYTES + 1))

    dispatch_calls = []
    monkeypatch.setattr(
        activity, "_dispatch_background_prune", lambda path: dispatch_calls.append(path)
    )

    activity._maybe_prune(log)
    activity._maybe_prune(log)
    activity._maybe_prune(log)

    assert len(dispatch_calls) == 1, "debounce marker should suppress repeat dispatches"


def test_maybe_prune_redispatches_in_a_new_debounce_window(patch_install_dir: Path, monkeypatch):
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * (activity._PRUNE_SIZE_BYTES + 1))

    dispatch_calls = []
    monkeypatch.setattr(
        activity, "_dispatch_background_prune", lambda path: dispatch_calls.append(path)
    )
    fake_now = [1_700_000_000.0]
    monkeypatch.setattr(activity.time, "time", lambda: fake_now[0])

    activity._maybe_prune(log)
    assert len(dispatch_calls) == 1

    fake_now[0] += 10  # still inside the same window
    activity._maybe_prune(log)
    assert len(dispatch_calls) == 1

    fake_now[0] += activity._PRUNE_DEBOUNCE_SECONDS  # a fresh window
    activity._maybe_prune(log)
    assert len(dispatch_calls) == 2


def test_claim_prune_marker_is_exclusive_per_window(patch_install_dir: Path):
    """Each debounce window has exactly one winner: claiming it twice for
    the same window returns ``True`` then ``False``."""
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    first = activity._claim_prune_marker(log)
    second = activity._claim_prune_marker(log)

    assert first is True
    assert second is False


def test_claim_prune_marker_cleans_up_only_beyond_the_grace_window(
    patch_install_dir: Path, monkeypatch
):
    """A marker exactly one window behind ("current - 1") is preserved, not
    cleaned up -- it may still be an in-flight claim by a caller that read
    the clock right at the previous window's tail and was then descheduled
    before completing its own exclusive create. Only a marker two or more
    windows behind is safe to remove."""
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    fake_now = [1_700_000_000.0]
    monkeypatch.setattr(activity.time, "time", lambda: fake_now[0])
    assert activity._claim_prune_marker(log) is True
    oldest_marker = activity._prune_marker_path(log)

    fake_now[0] += activity._PRUNE_DEBOUNCE_SECONDS  # one window later
    assert activity._claim_prune_marker(log) is True
    assert oldest_marker.exists(), (
        "the immediately-preceding window's marker must survive -- it may "
        "still be a delayed caller's in-flight claim"
    )
    middle_marker = activity._prune_marker_path(log)

    fake_now[0] += activity._PRUNE_DEBOUNCE_SECONDS  # two windows later
    assert activity._claim_prune_marker(log) is True
    assert not oldest_marker.exists(), "two windows behind is safe to clean up"
    assert middle_marker.exists(), "still only one window behind -- preserved"


def test_claim_prune_marker_atomic_under_real_multiprocess_race(tmp_path: Path):
    """Many real OS processes (not threads, not sequential in-process calls)
    racing on the same debounce window, released together from a shared
    barrier so the claim attempts genuinely overlap: exactly one may win."""
    import concurrent.futures
    import multiprocessing

    log = tmp_path / "activity.jsonl"
    n = 12
    with multiprocessing.Manager() as manager:
        barrier = manager.Barrier(n)
        with concurrent.futures.ProcessPoolExecutor(max_workers=n) as pool:
            results = list(
                pool.map(
                    _claim_prune_marker_in_subprocess,
                    [(str(log), barrier)] * n,
                )
            )

    assert results.count(True) == 1, f"expected exactly one winner, got {results}"


def test_maybe_prune_skips_small_file(patch_install_dir: Path, monkeypatch):
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"ts": "2026-01-01T00:00:00+00:00", "event": "x"}\n')

    dispatch_calls = []
    monkeypatch.setattr(
        activity, "_dispatch_background_prune", lambda path: dispatch_calls.append(path)
    )

    activity._maybe_prune(log)
    assert dispatch_calls == []


def test_activity_prune_worker_cmd_invokes_prune(patch_install_dir: Path, monkeypatch):
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        f'{{"ts": "{old_ts}", "event": "x", "worktree_id": "old"}}\n'
        f'{{"ts": "{new_ts}", "event": "x", "worktree_id": "new"}}\n'
    )

    class Args:
        path = str(log)
        retention_days = "7"

    rc = activity.cmd_activity_prune_worker(Args())
    assert rc == 0
    remaining = activity.read_events()
    assert len(remaining) == 1
    assert remaining[0]["worktree_id"] == "new"


def test_render_events_empty():
    assert activity.render_events([]) == "No activity recorded."


def test_render_events_aligns(patch_install_dir: Path):
    activity.log_event("worktree_created", worktree_id="wt-1", branch="b")
    activity.log_event(
        "session_started", worktree_id="wt-1", session_id="abcdef123456"
    )
    out = activity.render_events(activity.read_events())
    lines = out.splitlines()
    assert len(lines) == 2
    assert "worktree_created" in lines[0]
    assert "branch=b" in lines[0]
    # session id truncated to 8 chars
    assert "abcdef12" in lines[1]


def test_cmd_activity_log_appends(patch_install_dir: Path):
    class Args:
        event = "mux_attached"
        worktree_id = "wt-1"
        session_id = None
        source = "launcher"
        field = ("mux=join", "ignored_without_eq")

    rc = activity.cmd_activity_log(Args())
    assert rc == 0
    rec = activity.read_events()[0]
    assert rec["event"] == "mux_attached"
    assert rec["mux"] == "join"
    assert rec["source"] == "launcher"


def test_log_event_records_launch_id(patch_install_dir: Path):
    activity.log_event("launcher_started", worktree_id="wt-1", launch_id="abc123")
    rec = activity.read_events()[0]
    assert rec["launch_id"] == "abc123"
    # launch_id spine key present (None) when not supplied
    activity.log_event("session_started", worktree_id="wt-1")
    assert activity.read_events()[-1]["launch_id"] is None


def test_read_events_filters_by_launch_id(patch_install_dir: Path):
    activity.log_event("launcher_started", worktree_id="wt-1", launch_id="flow-a")
    activity.log_event("mux_attached", worktree_id="wt-1", launch_id="flow-a", mux="create")
    activity.log_event("launcher_started", worktree_id="wt-2", launch_id="flow-b")

    flow_a = activity.read_events(launch_id="flow-a")
    assert len(flow_a) == 2
    assert {r["event"] for r in flow_a} == {"launcher_started", "mux_attached"}
    assert len(activity.read_events(launch_id="flow-b")) == 1
    # launch_id composes with other filters
    assert len(activity.read_events(launch_id="flow-a", event="mux_attached")) == 1


def test_cmd_activity_log_forwards_launch_id(patch_install_dir: Path):
    class Args:
        event = "launcher_started"
        worktree_id = "wt-1"
        session_id = None
        launch_id = "corr-9"
        source = "launcher"
        field = ("mux=psmux", "setup_log=/tmp/setup-1.log")

    rc = activity.cmd_activity_log(Args())
    assert rc == 0
    rec = activity.read_events()[0]
    assert rec["launch_id"] == "corr-9"
    assert rec["mux"] == "psmux"
    assert rec["setup_log"] == "/tmp/setup-1.log"


def test_cmd_activity_log_resolves_worktree_id_from_cwd(patch_install_dir: Path, monkeypatch):
    """No --worktree-id: falls back to cwd resolution rather than logging a
    null-worktree_id entry invisible to `activity --worktree-id <id>`
    (copilot-extensions#2631)."""
    monkeypatch.setattr(
        "agent_worktrees.activity._infer_worktree_id_from_cwd", lambda: "wt-cwd",
    )

    class Args:
        event = "mux_attached"
        worktree_id = None
        session_id = None
        launch_id = None
        source = "launcher"
        field = ("mux=join",)

    rc = activity.cmd_activity_log(Args())
    assert rc == 0
    rec = activity.read_events()[0]
    assert rec["worktree_id"] == "wt-cwd"
    # explicit still wins over cwd resolution
    assert len(activity.read_events(worktree_id="wt-cwd")) == 1


def test_cmd_activity_log_fails_loudly_when_worktree_id_unresolvable(
    patch_install_dir: Path, monkeypatch, capsys,
):
    """Neither --worktree-id nor cwd resolves: fail loudly (non-zero exit +
    stderr) instead of silently appending an orphaned entry
    (copilot-extensions#2631)."""
    monkeypatch.setattr(
        "agent_worktrees.activity._infer_worktree_id_from_cwd", lambda: None,
    )

    class Args:
        event = "mux_attached"
        worktree_id = None
        session_id = None
        launch_id = None
        source = "launcher"
        field = ()

    rc = activity.cmd_activity_log(Args())
    assert rc != 0
    assert "could not determine worktree ID" in capsys.readouterr().err
    assert activity.read_events() == []


def test_cmd_activity_invalid_since(patch_install_dir: Path, capsys):
    class Args:
        since = "nonsense"
        worktree_id = None
        event = None
        lines = None
        json = False

    rc = activity.cmd_activity(Args())
    assert rc == 1


def test_log_event_never_raises_and_counts_failures(
    patch_install_dir: Path, monkeypatch, caplog
):
    """A write failure is swallowed (never raised to the caller) but must be
    detectable: log_event_failure_count() increments and a debug log fires --
    Phase 1's "not silently invisible" requirement."""
    before = activity.log_event_failure_count()

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)
    with caplog.at_level("DEBUG", logger="agent-worktrees"):
        activity.log_event("worktree_created", worktree_id="wt-1")  # must not raise

    assert activity.log_event_failure_count() == before + 1
    assert any("log_event" in r.message for r in caplog.records)


def test_log_event_writes_stage_mapped_events_into_durable_trace_store(
    patch_install_dir: Path, patch_active_project,
):
    """A stage-mapped event also lands in handoff_trace's durable store,
    namespaced by the in-process active project, alongside activity.jsonl."""
    activity.log_event("handoff_requested", worktree_id="wt-1", session_id="s1")
    durable = handoff_trace.read_trace("proj-a", "wt-1")
    assert len(durable) == 1
    assert durable[0]["event"] == "handoff_requested"
    assert durable[0]["stage"] == 6
    assert durable[0]["stage_name"] == "handoff_triggered"
    # Still present in the rolling machine-global log too -- purely additive.
    assert activity.read_events()[0]["event"] == "handoff_requested"


def test_log_event_does_not_write_unmapped_events_into_durable_trace_store(
    patch_install_dir: Path, patch_active_project,
):
    """An event with no stage mapping (e.g. worktree_reaped) is not a handoff
    lifecycle stage and must not clutter the durable per-worktree trace."""
    activity.log_event("worktree_reaped", worktree_id="wt-1")
    assert handoff_trace.read_trace("proj-a", "wt-1") == []


def test_log_event_skips_durable_trace_without_a_resolved_active_project(
    patch_install_dir: Path, monkeypatch,
):
    """No active project resolved (a rare ambient context) -- the event still
    lands in activity.jsonl, just not the durable per-project store, since
    the store cannot be namespaced without a project name."""
    monkeypatch.setattr("agent_worktrees.config.active_project", lambda: None)
    activity.log_event("handoff_requested", worktree_id="wt-1")
    assert activity.read_events()[0]["event"] == "handoff_requested"
    # No project -- nothing to read, and no exception raised getting there.
    assert handoff_trace.read_trace("wt-1", "wt-1") == []


def test_log_event_does_not_write_gated_out_events_into_durable_trace_store(
    patch_install_dir: Path, patch_active_project,
):
    """A gated-out event (e.g. a duplicate claim) carries no stage, so it
    must not be recorded in the durable per-stage trace either."""
    activity.log_event(
        "handoff_cutover_claim", worktree_id="wt-1", outcome="already-claimed"
    )
    assert handoff_trace.read_trace("proj-a", "wt-1") == []


def test_log_event_explicit_project_overrides_ambient_active_project(
    patch_install_dir: Path, patch_active_project,
):
    """agent-worktrees-authoritative-daemon Phase 3 (2026-09-26 PR review
    finding, round 2): a caller resolving its own real project explicitly
    (e.g. a verb dispatched via the resident daemon, whose own ambient
    ``cfg.active_project()`` need not match) must land the durable trace
    under ITS project, never the executing process's ambient one."""
    activity.log_event(
        "handoff_requested", worktree_id="wt-1", session_id="s1", project="proj-b"
    )
    assert handoff_trace.read_trace("proj-b", "wt-1")
    # The ambient project ("proj-a", per patch_active_project) must not have
    # received a copy -- this is a redirect, not an additional destination.
    assert handoff_trace.read_trace("proj-a", "wt-1") == []
