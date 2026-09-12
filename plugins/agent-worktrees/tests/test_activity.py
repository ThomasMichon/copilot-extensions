"""Tests for the worktree activity log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_worktrees import activity, handoff_trace


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


def test_log_event_stamps_known_handoff_stage(patch_install_dir: Path):
    activity.log_event("handoff_requested", worktree_id="wt-1", session_id="s1")
    rec = activity.read_events()[0]
    assert rec["stage"] == 6
    assert rec["stage_name"] == "handoff_triggered"


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
