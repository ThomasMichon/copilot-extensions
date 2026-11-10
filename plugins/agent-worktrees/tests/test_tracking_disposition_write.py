"""Tests for the ``status_disposition_write`` verb
(agent-worktrees-authoritative-daemon effort, Phase 3's first migrated call
site).

Covers the verb function directly (guards, the reactivation path, the
once-per-session ``status_reported`` event) and one end-to-end proof that
``tracking_write.dispatch`` reaches the exact same function via a real,
live daemon -- not just the in-process fallback ``test_status_write.py``
already exercises through ``_cmd_status_write``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import activity, tracking, tracking_disposition_write, tracking_write
from agent_worktrees import effort_focus as ef


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path) -> Path:
    record = tracking.WorktreeRecord(
        worktree_id="wt-disp",
        branch="worktree/wt-disp",
        worktree_path="/tmp/wt-disp",
        repo="example",
        machine="machine",
        platform="wsl",
        started_at="2026-09-26T00:00:00",
        last_resumed_at="2026-09-26T00:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at="2026-09-26T01:00:00",
        sessions=[],
    )
    path = tmp_tracking_dir / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    return path


def test_importing_the_module_registers_the_verb():
    assert "status_disposition_write" in tracking_write.registered_verbs()


@pytest.mark.parametrize("version", [None, 2, 3, "invalid"])
def test_full_title_selects_the_real_supported_receiver_or_same_code_fallback(
    record_path, monkeypatch, version,
):
    from agent_worktrees import disposition_history

    sent = []
    real_send = tracking_write._send_tracking_write_request

    def send(*args, **kwargs):
        sent.append(True)
        return real_send(*args, **kwargs)

    monkeypatch.setattr(tracking_write, "_send_tracking_write_request", send)
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    title = "A complete title crossing the real disposition transport " * 3
    try:
        fields = tracking_write.rendezvous_fields(server)
        if version is None:
            fields.pop("tracking_write_verb_versions", None)
        else:
            fields["tracking_write_verb_versions"]["status_disposition_write"] = version
        result = tracking_write.dispatch(
            "status_disposition_write", {
                "worktree_id": "wt-disp", "yaml_path": str(record_path),
                "title": title, "retain_full_title": True,
            },
            read_lock_data=lambda: fields, ensure_monitor=None,
            boot_wait_s=0, min_version=3,
        )
        if version == 3:
            fields["tracking_write_verb_versions"]["status_disposition_write"] = 2
            result = tracking_write.dispatch(
                "status_disposition_write", {
                    "worktree_id": "wt-disp", "yaml_path": str(record_path),
                    "title": title, "retain_full_title": True,
                },
                read_lock_data=lambda: fields, ensure_monitor=None,
                boot_wait_s=0, min_version=3,
            )
    finally:
        server.close()
    assert result["ok"] and result["title"] == title.strip()
    assert sent == ([True] if version == 3 else [])
    assert tracking.load_record(record_path).title == title.strip()
    assert [entry["title"] for entry in disposition_history.read(
        "wt-disp", tracking_path=record_path.parent,
    )] == [title.strip()] * (2 if version == 3 else 1)


def test_legacy_request_to_current_receiver_retains_its_short_contract_and_clear(record_path):
    title = "A legacy caller's title still uses its declared short projection"
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        fields = tracking_write.rendezvous_fields(server)
        for value, expected in ((title, tracking.cap_title(title)), (" \n ", None)):
            result = tracking_write.dispatch(
                "status_disposition_write", {
                    "worktree_id": "wt-disp", "yaml_path": str(record_path), "title": value,
                },
                read_lock_data=lambda: fields, ensure_monitor=None, min_version=2,
            )
            assert result["ok"] and result["title"] == expected
            assert tracking.load_record(record_path).title == expected
    finally:
        server.close()


def test_full_title_sent_then_unknown_does_not_repeat_the_committed_write(record_path, monkeypatch):
    from agent_worktrees import disposition_history

    real_send = tracking_write._send_tracking_write_request
    sent = []

    def lose_response(*args, **kwargs):
        sent.append(True)
        assert real_send(*args, **kwargs)["title"] == title
        raise tracking_write.AmbiguousWriteOutcome("response lost after admission")

    monkeypatch.setattr(tracking_write, "_send_tracking_write_request", lose_response)
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    title = "The title was committed once before its response became unavailable"
    try:
        fields = tracking_write.rendezvous_fields(server)
        with pytest.raises(tracking_write.AmbiguousWriteOutcome):
            tracking_write.dispatch(
                "status_disposition_write", {
                    "worktree_id": "wt-disp", "yaml_path": str(record_path),
                    "title": title, "retain_full_title": True,
                },
                read_lock_data=lambda: fields, ensure_monitor=None, min_version=3,
            )
    finally:
        server.close()
    assert sent == [True]
    assert tracking.load_record(record_path).title == title
    assert len(disposition_history.read("wt-disp", tracking_path=record_path.parent)) == 1


@pytest.mark.parametrize("extra", [
    {"retain_full_title": True, "title": "x" * 513},
    {"retain_full_title": "true", "title": "not accepted"},
])
def test_invalid_full_title_request_refuses_before_other_disposition_effects(record_path, extra):
    from agent_worktrees import disposition_history

    before = record_path.read_bytes()
    result = tracking_disposition_write.apply_status_disposition({
        "worktree_id": "wt-disp", "yaml_path": str(record_path),
        "summary": "must not change", "follow_up": True, "paused": True, **extra,
    })
    assert result["error"] == "invalid_title"
    assert record_path.read_bytes() == before
    assert disposition_history.read("wt-disp", tracking_path=record_path.parent) == []


def test_reactivates_finalized_worktree_on_follow_up(record_path):
    result = tracking_disposition_write.apply_status_disposition(
        {
            "worktree_id": "wt-disp",
            "yaml_path": str(record_path),
            "summary": "Begin the next change",
            "follow_up": True,
        }
    )
    assert result == {
        "ok": True,
        "follow_up": True,
        "paused": False,
        "title": None,
        "summary": "Begin the next change",
        "activity": "",
    }
    record = tracking.load_record(record_path)
    assert record.status == "active"
    assert record.completed_at is None
    assert record.follow_up is True


def test_summary_only_preserves_finalized_status(record_path):
    result = tracking_disposition_write.apply_status_disposition(
        {
            "worktree_id": "wt-disp",
            "yaml_path": str(record_path),
            "summary": "Keep the existing disposition",
        }
    )
    assert result["ok"] is True
    record = tracking.load_record(record_path)
    assert record.status == "finalized"
    assert record.completed_at == "2026-09-26T01:00:00"


def test_paused_does_not_reactivate_finalized_worktree(record_path):
    """Unlike `follow_up=True`, `paused=True` must never flip status/
    completed_at -- it's a purely informational, independent overlay."""
    result = tracking_disposition_write.apply_status_disposition(
        {
            "worktree_id": "wt-disp",
            "yaml_path": str(record_path),
            "summary": "Leave this open on purpose",
            "paused": True,
        }
    )
    assert result == {
        "ok": True,
        "follow_up": False,
        "paused": True,
        "title": None,
        "summary": "Leave this open on purpose",
        "activity": "",
    }
    record = tracking.load_record(record_path)
    assert record.status == "finalized"
    assert record.completed_at == "2026-09-26T01:00:00"
    assert record.paused is True


def test_terminal_managed_worktree_is_refused(record_path):
    record = tracking.load_record(record_path)
    record.kind = "system"
    assert record.kind in tracking.MANAGED_KINDS
    tracking.save_record(record, record_path)

    result = tracking_disposition_write.apply_status_disposition(
        {"worktree_id": "wt-disp", "yaml_path": str(record_path), "summary": "nope"}
    )
    assert result == {"error": "terminal_managed", "worktree_id": "wt-disp"}
    # Refused -- the record must be untouched.
    record = tracking.load_record(record_path)
    assert record.summary != "nope"


def test_resolving_follow_up_with_bound_effort_is_refused(record_path):
    record = tracking.load_record(record_path)
    record.active_effort = ef.make_active_effort(
        "efforts/active/some-effort/README.md", "Driver", "Phase 1"
    )
    tracking.save_record(record, record_path)

    result = tracking_disposition_write.apply_status_disposition(
        {"worktree_id": "wt-disp", "yaml_path": str(record_path), "follow_up": False}
    )
    assert result == {"error": "effort_bound"}


def test_status_reported_emitted_once_per_session(record_path):
    tracking_disposition_write.apply_status_disposition(
        {
            "worktree_id": "wt-disp",
            "yaml_path": str(record_path),
            "summary": "first",
            "session_id": "sess-1",
        }
    )
    tracking_disposition_write.apply_status_disposition(
        {
            "worktree_id": "wt-disp",
            "yaml_path": str(record_path),
            "summary": "second",
            "session_id": "sess-1",
        }
    )
    events = activity.read_events(worktree_id="wt-disp", event="status_reported")
    assert len(events) == 1
    assert events[0]["session_id"] == "sess-1"


def test_status_reported_not_emitted_without_session_id(record_path):
    tracking_disposition_write.apply_status_disposition(
        {"worktree_id": "wt-disp", "yaml_path": str(record_path), "summary": "no session"}
    )
    events = activity.read_events(worktree_id="wt-disp", event="status_reported")
    assert events == []


def test_history_sidecar_targets_the_record_own_directory_not_ambient_cwd(
    record_path, tmp_path, monkeypatch
):
    """2026-09-26 PR review finding: the verb may run inside the resident
    daemon process, whose own ambient active project need not match the
    project the dispatching CLI call actually targets. An ambient-scoped
    ``cfg.tracking_dir()`` history write would silently corrupt a
    *different* project's sidecar. Points the ambient project somewhere
    else entirely and proves the history entry still lands beside
    ``record_path``, never in the ambient directory."""
    ambient_dir = tmp_path / "some-other-projects-tracking-dir"
    ambient_dir.mkdir()
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: ambient_dir)

    tracking_disposition_write.apply_status_disposition(
        {"worktree_id": "wt-disp", "yaml_path": str(record_path), "summary": "scoped write"}
    )

    own_history = record_path.parent / "wt-disp.history.jsonl"
    ambient_history = ambient_dir / "wt-disp.history.jsonl"
    assert own_history.exists()
    assert "scoped write" in own_history.read_text(encoding="utf-8")
    assert not ambient_history.exists()


def test_status_reported_project_is_read_from_args_not_ambient(
    record_path, monkeypatch, monkeypatch_config
):
    """2026-09-26 PR review finding, round 2: the status_reported durable
    trace must land under the project the call site actually resolved
    (``args["project"]``), never the executing process's own ambient
    ``cfg.active_project()`` -- a resident daemon's ambient project need not
    match the project a dispatched request actually targets."""
    from agent_worktrees import handoff_trace

    monkeypatch.setattr("agent_worktrees.config.active_project", lambda: "daemon-ambient")
    tracking_disposition_write.apply_status_disposition(
        {
            "worktree_id": "wt-disp",
            "yaml_path": str(record_path),
            "summary": "scoped event",
            "session_id": "sess-proj",
            "project": "the-real-project",
        }
    )
    assert handoff_trace.read_trace("the-real-project", "wt-disp")
    assert handoff_trace.read_trace("daemon-ambient", "wt-disp") == []


def test_dispatch_reaches_the_verb_through_a_live_daemon(record_path):
    """End-to-end: unlike the direct-fallback path already covered by
    ``test_status_write.py``, this proves ``tracking_write.dispatch`` reaches
    ``apply_status_disposition`` via an actual ``CoalescingServer`` -- the
    two-process-shaped path Phase 3's real production traffic will use."""
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        result = tracking_write.dispatch(
            "status_disposition_write",
            {
                "worktree_id": "wt-disp",
                "yaml_path": str(record_path),
                "summary": "via daemon",
                "follow_up": True,
            },
            read_lock_data=lambda: lock_data,
            ensure_monitor=None,
        )
    finally:
        server.close()
    assert result["ok"] is True
    assert result["summary"] == "via daemon"
    record = tracking.load_record(record_path)
    assert record.summary == "via daemon"
    assert record.status == "active"


def test_apply_status_disposition_pushes_the_worktree_path_to_resident_push(
    record_path, monkeypatch
):
    """A successful write must notify resident_push with the record's own
    ``worktree_path`` -- the resident monitor's live segment cache is keyed
    by worktree path, not worktree id -- so that, when this transaction runs
    INSIDE the resident daemon process, OTHER sessions' status bars refresh
    on the monitor's very next loop iteration instead of waiting out the
    full periodic sweep interval."""
    from agent_worktrees import resident_push

    seen = []
    monkeypatch.setattr(resident_push, "notify", seen.append)

    tracking_disposition_write.apply_status_disposition(
        {"worktree_id": "wt-disp", "yaml_path": str(record_path), "summary": "push me"}
    )

    assert seen == ["/tmp/wt-disp"]


def test_apply_status_disposition_does_not_push_on_a_refused_write(
    record_path, monkeypatch
):
    """A guard rejection (terminal/effort-bound) never mutates the record --
    it must not push a stale/no-op refresh either."""
    from agent_worktrees import resident_push

    record = tracking.load_record(record_path)
    record.kind = "system"
    tracking.save_record(record, record_path)

    seen = []
    monkeypatch.setattr(resident_push, "notify", seen.append)

    tracking_disposition_write.apply_status_disposition(
        {"worktree_id": "wt-disp", "yaml_path": str(record_path), "summary": "nope"}
    )

    assert seen == []
